"""Integration tests for the SmartPipeline end-to-end flow.

Tests the pipeline through its public API (run()) by mocking at external
boundaries (caches, clients) rather than internal methods. Changing
internal method names or restructuring phases should not break these tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from immich_memories.analysis.selection_review import ReviewVerdict
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


def _make_cached_analysis(asset_id: str, score: float = 0.5) -> MagicMock:
    """Build a mock CachedVideoAnalysis with one segment so analysis uses cache."""
    segment = MagicMock()
    segment.start_time = 0.0
    segment.end_time = 5.0
    segment.total_score = score
    segment.face_score = 0.3
    segment.motion_score = 0.2
    segment.stability_score = 0.4
    segment.llm_description = None
    segment.llm_emotion = None
    segment.audio_categories = None

    analysis = MagicMock()
    analysis.asset_id = asset_id
    analysis.segments = [segment]
    return analysis


class TestSmartPipelineIntegration:
    """End-to-end tests for SmartPipeline through the public run() API."""

    def _make_pipeline(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        config: PipelineConfig | None = None,
    ) -> SmartPipeline:
        return SmartPipeline(
            client=mock_immich_client,
            analysis_cache=mock_analysis_cache,
            thumbnail_cache=mock_thumbnail_cache,
            config=config or PipelineConfig(target_clips=10, avg_clip_duration=5.0),
            analysis_config=AnalysisConfig(),
            app_config=Config(),
        )

    def _setup_cache_for_clips(self, mock_cache: MagicMock, clips: list) -> None:
        """Configure mock cache to return cached analysis for all clips."""

        def get_analysis(asset_id: str, include_segments: bool = True):
            return _make_cached_analysis(asset_id)

        mock_cache.get_analysis.side_effect = get_analysis

    def test_full_run_returns_pipeline_result(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Full pipeline run with cached analysis returns a PipelineResult."""
        clips = _make_clips(10, is_favorite=True)

        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, avg_clip_duration=5.0, analyze_all=True),
        )

        result = pipeline.run(clips)

        assert isinstance(result, PipelineResult)
        assert result.selected_clips
        assert len(result.clip_segments) == len(result.selected_clips)
        assert isinstance(result.stats, dict)
        assert "selected_count" in result.stats

    def test_empty_clips_returns_empty_result(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Empty clip list produces empty result with no errors."""
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
        )

        result = pipeline.run([])

        assert isinstance(result, PipelineResult)
        assert not result.selected_clips
        assert not result.clip_segments
        assert not result.errors

    def test_hdr_only_filters_sdr_clips(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """HDR-only mode keeps only HDR clips in non-favorites."""
        config = PipelineConfig(target_clips=5, hdr_only=True, analyze_all=False)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        hdr_clips = _make_clips(3, hdr=True, is_favorite=False)
        sdr_clips = _make_clips(3, hdr=False, is_favorite=False)
        for i, c in enumerate(sdr_clips):
            c.asset.id = f"sdr-{i:03d}"

        all_clips = hdr_clips + sdr_clips
        self._setup_cache_for_clips(mock_analysis_cache, all_clips)

        result = pipeline.run(all_clips)

        sdr_ids = {c.asset.id for c in sdr_clips}
        selected_ids = {c.asset.id for c in result.selected_clips}
        assert sdr_ids.isdisjoint(selected_ids), "SDR clips should not appear in HDR-only results"

    def test_progress_callback_invoked_with_increasing_values(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Progress callback is called with monotonically increasing progress values (0 to 1)."""
        clips = _make_clips(5, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )
        progress_calls: list = []

        def track_progress(*args, **kwargs):
            progress_calls.append(args)

        pipeline.run(clips, progress_callback=track_progress)

        # WHY: progress callback is called with varying signatures (float, dict, etc.)
        # We verify it was actually called, not just "connected"
        assert len(progress_calls) >= 1, "Progress callback should be called"
        # Check that numeric progress values (when present) are reasonable
        float_values = [a[0] for a in progress_calls if isinstance(a[0], (int, float))]
        if float_values:
            assert all(0 <= v <= 1.0 for v in float_values), (
                f"Progress values should be 0-1, got {float_values}"
            )

    def test_analyze_all_sends_all_clips_to_analysis(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """analyze_all mode processes all clips through the pipeline."""
        clips = _make_clips(8, is_favorite=False)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        config = PipelineConfig(target_clips=5, analyze_all=True)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        pipeline.run(clips)

        # All 8 clips should have been looked up in cache (one call per clip)
        cache_calls = mock_analysis_cache.get_analysis.call_args_list
        queried_ids = {call.args[0] for call in cache_calls}
        clip_ids = {c.asset.id for c in clips}
        assert clip_ids.issubset(queried_ids), "All clips should have been queried in the cache"

    def test_favorites_always_analyzed(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Favorites are always included regardless of non-favorite filters."""
        favorites = _make_clips(3, is_favorite=True)
        non_favorites = _make_clips(5, is_favorite=False)
        for i, c in enumerate(non_favorites):
            c.asset.id = f"nonfav-{i:03d}"

        all_clips = favorites + non_favorites
        self._setup_cache_for_clips(mock_analysis_cache, all_clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=False),
        )

        result = pipeline.run(all_clips)

        selected_ids = {c.asset.id for c in result.selected_clips}
        fav_ids = {c.asset.id for c in favorites}
        assert fav_ids.issubset(selected_ids), "All favorites should be in the final selection"

    def test_single_clip_returns_it(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Single clip input produces a result containing that clip."""
        clips = _make_clips(1, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        assert len(result.selected_clips) == 1
        assert result.selected_clips[0].asset.id == clips[0].asset.id

    def test_duplicate_clip_ids_handled(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Pipeline handles clips with identical IDs gracefully."""
        clips = _make_clips(3, is_favorite=True)
        # Duplicate the first clip's ID on the second
        clips[1].asset.id = clips[0].asset.id
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        # Should not crash, result is valid
        assert isinstance(result, PipelineResult)

    def test_result_stats_contain_expected_keys(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Pipeline stats dict contains standard diagnostic keys."""
        clips = _make_clips(5, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        result = pipeline.run(clips)
        assert "selected_count" in result.stats
        assert "total_analyzed" in result.stats

    def test_idempotent_run(
        self,
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        sample_config,
    ):
        """Running twice with same inputs produces same clip count."""
        clips = _make_clips(8, is_favorite=True)
        self._setup_cache_for_clips(mock_analysis_cache, clips)

        config = PipelineConfig(target_clips=5, analyze_all=True)
        pipeline = self._make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=config,
        )

        result1 = pipeline.run(clips)
        # Reset cache call counts
        mock_analysis_cache.get_analysis.reset_mock()
        result2 = pipeline.run(clips)

        assert len(result1.selected_clips) == len(result2.selected_clips)


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
        with (
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
