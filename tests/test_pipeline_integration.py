"""Integration tests for the SmartPipeline end-to-end flow.

The public run() tests use controlled editorial answers with real source
normalization and demand. The remaining classes exercise legacy component APIs
explicitly; they are not the production selection route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.editorial_source_route import EditorialSourcePlan, metadata_demand
from immich_memories.analysis.selection_review import ReviewVerdict
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.smart_pipeline import (
    PipelineConfig,
    PipelineResult,
    SmartPipeline,
)
from immich_memories.config_loader import Config
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_clip


def _make_clips(count: int, *, is_favorite: bool = False, hdr: bool = False) -> list:
    """Create a list of synthetic clips spread across months."""
    base = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    clips = []
    for i in range(count):
        dt = base + timedelta(days=i * 7)
        clips.append(
            make_clip(
                f"clip-{i:03d}",
                width=1920,
                height=1080,
                duration=10.0,
                is_favorite=is_favorite,
                color_transfer="arib-std-b67" if hdr else None,
                file_created_at=dt,
            )
        )
    return clips


class _ControlledSourcePlanner:
    """Control only the editorial answer; retain real source eligibility and demand."""

    def __init__(self, selections=None):
        self.selections = selections
        self.calls = []
        self.demands = []

    def plan_source(
        self, sources, *, trace, include_live_photos=True, hdr_only=False, on_stage=None
    ):
        self.calls.append((sources, include_live_photos, hdr_only))
        if on_stage:
            on_stage("Preparing source metadata")
        prepared = prepare_editorial_source(
            EditorialSelectionRequest(SourceScope()),
            EditorialDependencies(source_fetcher=lambda _: sources),
            trace=trace,
        )
        rows = metadata_demand(prepared, sources, photo_seconds=4, hdr_only=hdr_only)
        self.demands.append(rows)
        if on_stage:
            on_stage("Editing the memory")
        selections = self.selections
        if selections is None:
            selections = _selections(row.clip.asset.id for row in rows)
        return EditorialSourcePlan(rows, EditorialPlan(selections=selections))


def _selections(ids):
    return tuple(EditorialSelection(key, 1.25, 4.75) for key in ids)


@pytest.fixture
def source_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("production source planning entered legacy analysis or selection")

    def build(*, selections=None, config=None):
        planner = _ControlledSourcePlanner(selections)
        pipeline = SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=config or PipelineConfig(target_clips=5),
            analysis_config=AnalysisConfig(),
            app_config=Config(),
            planner=planner,
        )
        monkeypatch.setattr(pipeline, "run_analysis", forbidden)
        monkeypatch.setattr(pipeline, "run_selection", forbidden)
        monkeypatch.setattr(pipeline.analyzer, "phase_analyze", forbidden)
        monkeypatch.setattr(pipeline.refiner, "phase_refine", forbidden)
        monkeypatch.setattr(mock_analysis_cache, "get_analysis", forbidden)
        return pipeline, planner

    return build


class TestSmartPipelineIntegration:
    """Public run() conserves the source planner's final answer without legacy work."""

    def test_full_run_returns_pipeline_result(self, source_pipeline):
        clips = _make_clips(10, is_favorite=True)
        expected = _selections((clips[3].asset.id, clips[0].asset.id))
        pipeline, planner = source_pipeline(selections=expected)

        result = pipeline.run(clips)

        assert isinstance(result, PipelineResult)
        assert planner.calls == [(clips, True, False)]
        assert planner.calls[0][0] is clips
        assert result.selected_clips == [clips[3], clips[0]]
        assert result.editorial_selections == expected
        assert result.clip_segments == {row.asset_id: (1.25, 4.75) for row in expected}
        assert result.stats["selected_count"] == 2

    def test_empty_clips_returns_empty_result(self, source_pipeline):
        pipeline, planner = source_pipeline()

        result = pipeline.run([])

        assert planner.calls == [([], True, False)]
        assert isinstance(result, PipelineResult)
        assert not result.selected_clips
        assert not result.clip_segments
        assert not result.editorial_selections
        assert not result.errors
        assert result.stats["source_candidate_count"] == 0

    @pytest.mark.parametrize("favorite", [False, True])
    def test_hdr_only_filters_sdr_clips(self, source_pipeline, favorite):
        hdr = _make_clips(3, hdr=True, is_favorite=favorite)
        sdr = _make_clips(3, hdr=False, is_favorite=favorite)
        for i, clip in enumerate(sdr):
            clip.asset.id = f"sdr-{i:03d}"
        pipeline, planner = source_pipeline(config=PipelineConfig(hdr_only=True))

        result = pipeline.run(hdr + sdr)

        expected = [clip.asset.id for clip in hdr]
        assert planner.calls[0][2] is True
        assert [row.clip.asset.id for row in planner.demands[0]] == expected
        assert [clip.asset.id for clip in result.selected_clips] == expected

    def test_progress_reports_ordered_source_stages(self, source_pipeline, monkeypatch):
        clock = iter(100.0 + n for n in range(100))
        monkeypatch.setattr("immich_memories.analysis.progress.time.time", lambda: next(clock))
        pipeline, _ = source_pipeline()
        events = []

        pipeline.run(_make_clips(2), progress_callback=events.append)

        assert [event["current_phase"] for event in events] == [
            "Preparing editorial evidence",
            "Preparing source metadata",
            "Editing the memory",
            "Editorial selection complete",
        ]
        assert [event["status"] for event in events] == ["running"] * 3 + ["complete"]
        elapsed = [event["elapsed_seconds"] for event in events]
        assert elapsed == sorted(elapsed)
        assert all(event["indeterminate"] for event in events)
        assert not any("progress_fraction" in event for event in events)

    @pytest.mark.parametrize("analyze_all", [False, True])
    def test_all_eligible_sources_reach_editor_without_legacy_analysis(
        self, source_pipeline, analyze_all
    ):
        clips = _make_clips(8)
        pipeline, planner = source_pipeline(
            config=PipelineConfig(target_clips=2, analyze_all=analyze_all)
        )

        result = pipeline.run(clips)

        assert [row.clip.asset.id for row in planner.demands[0]] == [c.asset.id for c in clips]
        assert all(row.analyzed is False and row.score == 0 for row in planner.demands[0])
        assert result.stats["total_analyzed"] == result.stats["legacy_deep_analysis_count"] == 0
        assert pipeline.last_deep_analysis_count == 0

    def test_favorite_evidence_is_preserved_without_forcing_final_inclusion(self, source_pipeline):
        clips = _make_clips(4)
        clips[0].asset.is_favorite = True
        clips[1].asset.is_favorite = True
        expected = _selections((clips[3].asset.id,))
        pipeline, planner = source_pipeline(selections=expected)

        result = pipeline.run(clips)

        assert {row.clip.asset.id for row in planner.demands[0] if row.clip.asset.is_favorite} == {
            clips[0].asset.id,
            clips[1].asset.id,
        }
        assert result.editorial_selections == expected
        assert [clip.asset.id for clip in result.selected_clips] == [clips[3].asset.id]

    @pytest.mark.parametrize("selected", [False, True])
    def test_single_clip_follows_explicit_editorial_answer(self, source_pipeline, selected):
        clips = _make_clips(1, is_favorite=True)
        expected = _selections((clips[0].asset.id,)) if selected else ()
        pipeline, _ = source_pipeline(selections=expected)

        result = pipeline.run(clips)

        assert result.editorial_selections == expected
        assert result.selected_clips == (clips if selected else [])
        assert result.clip_segments == ({clips[0].asset.id: (1.25, 4.75)} if selected else {})

    def test_identical_duplicate_source_is_coalesced(self, source_pipeline):
        clips = _make_clips(2)
        sources = [clips[0], clips[0].model_copy(deep=True), clips[1]]
        pipeline, planner = source_pipeline()

        result = pipeline.run(sources)

        assert len(planner.calls[0][0]) == 3
        assert [row.clip.asset.id for row in planner.demands[0]] == [c.asset.id for c in clips]
        assert [clip.asset.id for clip in result.selected_clips] == [c.asset.id for c in clips]

    def test_conflicting_duplicate_source_is_rejected(self, source_pipeline):
        clips = _make_clips(3)
        clips[1].asset.id = clips[0].asset.id
        pipeline, planner = source_pipeline()

        with pytest.raises(
            ValueError, match="conflicting source representations for asset clip-000"
        ):
            pipeline.run(clips)

        assert planner.demands == []

    def test_result_stats_describe_source_route_without_legacy_analysis(self, source_pipeline):
        clips = _make_clips(5)
        pipeline, _ = source_pipeline(selections=_selections((clips[0].asset.id,)))

        result = pipeline.run(clips)

        assert result.stats["selection_route"] == "editorial-source"
        assert result.stats["selected_count"] == 1
        assert result.stats["source_candidate_count"] == 5
        assert result.stats["total_analyzed"] == result.stats["legacy_deep_analysis_count"] == 0

    def test_repeated_run_preserves_exact_controlled_answer(self, source_pipeline):
        clips = _make_clips(8)
        expected = _selections((clips[6].asset.id, clips[2].asset.id))
        pipeline, planner = source_pipeline(selections=expected)

        first = pipeline.run(clips)
        second = pipeline.run(clips)

        assert len(planner.calls) == 2
        assert first.editorial_selections == second.editorial_selections == expected
        assert (
            first.clip_segments
            == second.clip_segments
            == {row.asset_id: (1.25, 4.75) for row in expected}
        )
        assert first.selected_clips == second.selected_clips == [clips[6], clips[2]]

    @pytest.mark.parametrize("empty", [False, True])
    def test_missing_source_planner_is_rejected_without_legacy_fallback(
        self, source_pipeline, empty
    ):
        pipeline, _ = source_pipeline()
        pipeline._planner = None

        with pytest.raises(RuntimeError, match="no production editorial source route"):
            pipeline.run([] if empty else _make_clips(1))


class TestVerifyPass:
    """#468: what ships must be analyzed — a fallback score is a placeholder,
    not a rank, and the verify pass replaces it with the real thing."""

    def _make_pipeline(self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache):
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=AnalysisConfig(),
            app_config=Config(),
        )

    def _fallback(self, pipeline, clip, score: float):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score, analyzed=False)

    def _analyzed(self, clip, score: float):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score)

    def test_a_shipped_fallback_clip_is_analyzed_before_assembly(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(3)
        candidates = [
            self._analyzed(clips[0], 0.8),
            self._analyzed(clips[1], 0.7),
            self._fallback(pipeline, clips[2], 0.4),
        ]

        # WHY: phase_analyze downloads and scores real video — the external boundary
        verified = self._analyzed(clips[2], 0.75)
        pipeline.analyzer.phase_analyze = MagicMock(return_value=[verified])

        result = pipeline.run_selection(candidates)

        analyzed_ids = [c.asset.id for c in pipeline.analyzer.phase_analyze.call_args[0][0]]
        assert analyzed_ids == [clips[2].asset.id]
        assert clips[2].asset.id in {c.asset.id for c in result.selected_clips}

    def test_a_verified_clip_whose_score_collapses_is_replaced(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(3)
        # tight budget: only 2 of 3 fit; the fallback's optimistic 0.9 wins pass 1
        pipeline.config.target_duration_seconds = 10.0
        candidates = [
            self._analyzed(clips[0], 0.8),
            self._analyzed(clips[1], 0.7),
            self._fallback(pipeline, clips[2], 0.9),
        ]

        # WHY: real analysis is the boundary; it reveals the clip is bad (feet)
        collapsed = self._analyzed(clips[2], 0.05)
        pipeline.analyzer.phase_analyze = MagicMock(return_value=[collapsed])

        result = pipeline.run_selection(candidates)

        selected = {c.asset.id for c in result.selected_clips}
        assert clips[2].asset.id not in selected

    def test_dry_run_planning_never_analyzes(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(2)
        candidates = [
            self._analyzed(clips[0], 0.8),
            self._fallback(pipeline, clips[1], 0.4),
        ]
        pipeline.analyzer.phase_analyze = MagicMock(
            side_effect=AssertionError("dry-run must stay local")
        )

        result = pipeline.run_selection(candidates, verify=False)

        assert result.selected_clips


class TestSelectionJudge:
    """#468 judge slice / #463: after verification, selection must pass a
    global quality gate — no sub-floor member ships, and the chronological
    ending cannot be the one weak clip in the timeline."""

    def _make_pipeline(self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache):
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=AnalysisConfig(),
            app_config=Config(),
        )

    def _analyzed(self, clip, score: float):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score)

    def test_a_sub_floor_clip_never_ships(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """A clip whose REAL score is junk (feet, pocket, ground) is dropped
        and the refiner refills from the pool."""
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(4)
        # room for everything: only a floor, not budget pressure, can reject
        pipeline.config.target_duration_seconds = 25.0
        candidates = [
            self._analyzed(clips[0], 0.8),
            self._analyzed(clips[1], 0.7),
            self._analyzed(clips[2], 0.05),  # junk, but analyzed — floor's job
            self._analyzed(clips[3], 0.75),
        ]

        result = pipeline.run_selection(candidates)

        assert clips[2].asset.id not in {c.asset.id for c in result.selected_clips}

    def test_a_weak_ending_is_replaced_by_reselection(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """The chronologically-last clip was both the minimum and far below the
        mean — the most visible slot must not hold the worst clip (#463)."""
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(4)
        pipeline.config.target_duration_seconds = 15.0
        weak_last = clips[3]
        candidates = [
            self._analyzed(clips[0], 0.9),
            self._analyzed(clips[1], 0.85),
            self._analyzed(clips[2], 0.8),
            self._analyzed(weak_last, 0.4),  # above floor, but a weak ending
        ]

        result = pipeline.run_selection(candidates)

        selected = sorted(result.selected_clips, key=lambda c: c.asset.file_created_at)
        assert selected, "selection must not be empty"
        assert selected[-1].asset.id != weak_last.asset.id

    def test_a_uniformly_scored_selection_is_left_alone(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """The judge fixes outliers; it must not churn a healthy selection."""
        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(3)
        candidates = [self._analyzed(c, 0.7) for c in clips]

        result = pipeline.run_selection(candidates)

        assert {c.asset.id for c in result.selected_clips} == {c.asset.id for c in clips}


class TestHolisticReview:
    """#468: one LLM pass over the finished cut — redundancy the scores
    cannot see. Optional by construction: no LLM, no changes."""

    def _make_pipeline(self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, **cfg):
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=AnalysisConfig(),
            app_config=Config(**cfg),
        )

    def _analyzed(self, clip, score: float = 0.7):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score)

    def test_llm_flagged_redundancy_is_dropped(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        from unittest.mock import patch

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            content_analysis={"enabled": True},
        )
        clips = _make_clips(3)

        members = [self._analyzed(c) for c in clips]

        # WHY two mocks: the LLM call is one external boundary, and the verify
        # pass now analyzes any selected clip the review would otherwise judge
        # blind — a second boundary this fixture cannot serve.
        # WHY: see the note above — the review LLM and the verify-pass analyzer are both external.
        with (
            # WHY: the review verdict comes from the model provider; a fixed drop list stands in.
            patch(
                "immich_memories.analysis.selection_review.review_selection",
                return_value=ReviewVerdict(drops=[clips[1].asset.id]),
            ),
            patch.object(
                pipeline.analyzer,
                "phase_analyze",
                side_effect=lambda c, _t: [
                    m for m in members if m.clip.asset.id in {x.asset.id for x in c}
                ],
            ),
        ):
            result = pipeline.run_selection(members)

        assert clips[1].asset.id not in {c.asset.id for c in result.selected_clips}

    def test_without_content_analysis_no_llm_is_consulted(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        from unittest.mock import patch

        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(3)

        # WHY: review_selection wraps the external LLM; consulting it here is the bug
        with patch(
            "immich_memories.analysis.selection_review.review_selection",
            side_effect=AssertionError("LLM must not be consulted"),
        ):
            result = pipeline.run_selection([self._analyzed(c) for c in clips])

        assert len(result.selected_clips) == 3


class TestQualityStagesAreOneLoop:
    """Found by the 2026-08-21 live demo: a judge/review drop re-selects, the
    re-selection admits a NEW fallback clip, and nothing verified it — two
    0.21 unverified clips shipped. Verify, judge and review must iterate
    together until the selection is stable."""

    def _make_pipeline(self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache):
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=AnalysisConfig(),
            app_config=Config(),
        )

    def test_a_clip_admitted_by_reselection_is_verified_before_shipping(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        clips = _make_clips(4)
        pipeline.config.target_duration_seconds = 15.0
        weak_end = clips[3]
        spare = clips[2]
        candidates = [
            ClipWithSegment(clip=clips[0], start_time=0, end_time=5, score=0.9),
            ClipWithSegment(clip=clips[1], start_time=0, end_time=5, score=0.85),
            # the spare that re-selection will admit — a fallback guess
            ClipWithSegment(clip=spare, start_time=0, end_time=5, score=0.5, analyzed=False),
            # weak ending: judged out on the first pass
            ClipWithSegment(clip=weak_end, start_time=0, end_time=5, score=0.31),
        ]

        verified_ids = []

        def fake_analyze(to_analyze, tracker):
            verified_ids.extend(c.asset.id for c in to_analyze)
            return [
                ClipWithSegment(clip=c, start_time=0, end_time=5, score=0.7) for c in to_analyze
            ]

        # WHY: phase_analyze downloads and scores real video — the boundary
        pipeline.analyzer.phase_analyze = fake_analyze

        result = pipeline.run_selection(candidates)

        selected = {c.asset.id for c in result.selected_clips}
        if spare.asset.id in selected:
            assert spare.asset.id in verified_ids, (
                "a re-selection admitted an unverified clip and nothing analyzed it"
            )

    def test_a_review_drop_stays_dropped_through_stabilization(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """The LLM dropped it; a later verify re-refine must not resurrect it."""
        from unittest.mock import patch

        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        pipeline = self._make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
        )
        pipeline._app_config.content_analysis.enabled = True
        clips = _make_clips(4)
        redundant = clips[1]
        candidates = [
            ClipWithSegment(clip=clips[0], start_time=0, end_time=5, score=0.9),
            ClipWithSegment(clip=redundant, start_time=0, end_time=5, score=0.85),
            ClipWithSegment(clip=clips[2], start_time=0, end_time=5, score=0.8),
            # unverified spare: forces a verify re-refine AFTER the review
            ClipWithSegment(clip=clips[3], start_time=0, end_time=5, score=0.7, analyzed=False),
        ]

        def fake_analyze(to_analyze, tracker):  # WHY: real analysis is the boundary
            return [
                ClipWithSegment(clip=c, start_time=0, end_time=5, score=0.75) for c in to_analyze
            ]

        pipeline.analyzer.phase_analyze = fake_analyze
        # WHY: review_selection wraps the external LLM
        with patch(
            "immich_memories.analysis.selection_review.review_selection",
            return_value=ReviewVerdict(drops=[redundant.asset.id]),
        ):
            result = pipeline.run_selection(candidates)

        assert redundant.asset.id not in {c.asset.id for c in result.selected_clips}
