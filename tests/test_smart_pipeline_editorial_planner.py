"""Production contract between editorial planning and the video pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from immich_memories.analysis import selection_trace
from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig, SmartPipeline
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_clip


@dataclass
class _FixedPlanner:
    answer: EditorialPlan
    seen: tuple[ClipWithSegment, ...] | None = None
    seen_trace: selection_trace.Trace | None = None

    def plan(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        trace: selection_trace.Trace,
    ) -> EditorialPlan:
        self.seen = candidates
        self.seen_trace = trace
        return self.answer


class _ForbiddenLegacyStage:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"legacy selection must not consult {name}")


@dataclass
class _ReplanningPlanner:
    calls: list[tuple[ClipWithSegment, ...]] = field(default_factory=list)
    analyzed_states: list[tuple[bool, ...]] = field(default_factory=list)

    def plan(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        trace: selection_trace.Trace,
    ) -> EditorialPlan:
        del trace
        self.calls.append(candidates)
        self.analyzed_states.append(tuple(candidate.analyzed for candidate in candidates))
        return EditorialPlan(selections=(EditorialSelection(candidates[0].clip.asset.id),))


@dataclass
class _RecordingAnalyzer:
    calls: list[list] = field(default_factory=list)
    closed: int = 0

    def phase_analyze(self, clips: list, _tracker) -> list[ClipWithSegment]:
        self.calls.append(clips)
        return [ClipWithSegment(clip, 1.0, 4.0, 0.96, analyzed=True) for clip in clips]

    def close(self) -> None:
        self.closed += 1


@dataclass
class _FailedAnalyzer(_RecordingAnalyzer):
    def phase_analyze(self, clips: list, _tracker) -> list[ClipWithSegment]:
        self.calls.append(clips)
        return [ClipWithSegment(clip, 0.0, 2.0, 0.0, analyzed=False) for clip in clips]


def _pipeline(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
    *,
    planner: _FixedPlanner,
) -> SmartPipeline:
    return SmartPipeline(
        client=mock_immich_client,
        analysis_cache=mock_analysis_cache,
        thumbnail_cache=mock_thumbnail_cache,
        config=PipelineConfig(target_clips=1),
        analysis_config=AnalysisConfig(),
        app_config=Config(),
        planner=planner,
    )


def test_editorial_plan_is_the_finished_cut_without_legacy_refinement(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    first = ClipWithSegment(make_clip("first"), 1.25, 4.75, 0.91)
    second = ClipWithSegment(make_clip("second", duration=8.0), 2.5, 8.0, 0.82)
    third = ClipWithSegment(make_clip("third"), 0.0, 3.0, 0.73)
    candidates = [first, second, third]
    planner = _FixedPlanner(
        EditorialPlan(selections=(EditorialSelection("third"), EditorialSelection("first")))
    )
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )
    pipeline.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.quality.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]

    result = pipeline.run_selection(candidates)

    assert planner.seen is not None
    assert all(seen is original for seen, original in zip(planner.seen, candidates, strict=True))
    assert result.selected_clips == [third.clip, first.clip]
    assert result.selected_clips[0] is third.clip
    assert result.selected_clips[1] is first.clip
    assert result.clip_segments == {"third": (0.0, 3.0), "first": (1.25, 4.75)}


def test_editorial_planner_rejects_duplicate_input_asset_ids(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    first = ClipWithSegment(make_clip("same"), 0.0, 2.0, 0.8)
    duplicate = ClipWithSegment(make_clip("same"), 4.0, 7.0, 0.7)
    planner = _FixedPlanner(EditorialPlan(selections=(EditorialSelection("same"),)))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with pytest.raises(ValueError, match="duplicate input asset IDs.*same"):
        pipeline.run_selection([first, duplicate])


def test_editorial_planner_rejects_duplicate_output_asset_ids(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("chosen"), 0.0, 3.0, 0.8)
    planner = _FixedPlanner(
        EditorialPlan(selections=(EditorialSelection("chosen"), EditorialSelection("chosen")))
    )
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with pytest.raises(ValueError, match="duplicate output asset IDs.*chosen"):
        pipeline.run_selection([candidate])


def test_editorial_planner_rejects_output_outside_the_input_pool(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("candidate"), 0.0, 3.0, 0.8)
    planner = _FixedPlanner(EditorialPlan(selections=(EditorialSelection("outside-the-run"),)))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with pytest.raises(ValueError, match="outside the input pool.*outside-the-run"):
        pipeline.run_selection([candidate])


@pytest.mark.parametrize("from_structure_abstention", [False, True])
def test_valid_empty_editorial_plan_does_not_fall_back_to_legacy_selection(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
    from_structure_abstention,
) -> None:
    candidate = ClipWithSegment(make_clip("available"), 0.0, 3.0, 0.8)
    plan = EditorialPlan()
    if from_structure_abstention:
        from immich_memories.analysis.editorial_runtime_backend import _plan_from_structure_result
        from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult

        structure = StructurePlanningResult(
            {
                "status": "insufficient_material",
                "carriers": [
                    {"asset_id": "available", "taken": "2025-01-01", "kind": "still"},
                ],
            },
            "contract",
            "diagnostic sheet",
            {},
        )
        plan = _plan_from_structure_result(structure, allowed_ids={"available"})
    planner = _FixedPlanner(plan)
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )
    pipeline.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.quality.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]

    result = pipeline.run_selection([candidate])

    assert result.selected_clips == []
    assert result.clip_segments == {}
    assert result.stats["selected_count"] == 0


def test_unavailable_editorial_plan_warns_and_falls_back_to_legacy_selection(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidate = ClipWithSegment(make_clip("legacy-can-use-this"), 0.0, 3.0, 0.8)
    planner = _FixedPlanner(EditorialPlan(unavailable_reason="episode evidence is cold"))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    # WHY: nothing is replaced — a real trace context and the log capture record the run.
    with (
        selection_trace.tracing() as recorded,
        caplog.at_level(logging.WARNING),
    ):
        result = pipeline.run_selection([candidate], verify=False)

    assert result.selected_clips == [candidate.clip]
    assert "using legacy selection: episode evidence is cold" in caplog.text
    assert recorded.warnings == [
        "Editorial planner unavailable; using legacy selection: episode evidence is cold"
    ]


def test_editorial_plan_is_measured_against_the_favourite_law(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    favourite = ClipWithSegment(make_clip("favourite", is_favorite=True), 0.0, 3.0, 0.9)
    neighbour = ClipWithSegment(make_clip("neighbour"), 0.0, 3.0, 0.8)
    planner = _FixedPlanner(EditorialPlan(selections=(EditorialSelection("neighbour"),)))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with selection_trace.tracing() as recorded:
        pipeline.run_selection([favourite, neighbour])

    assert recorded.lost_favourites is not None
    assert recorded.lost_favourites[0].favourites == ("favourite",)
    assert recorded.lost_favourites[0].shipped == ("neighbour",)


def test_editorial_planner_receives_the_active_selection_trace(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("traced"), 0.0, 3.0, 0.8)
    planner = _FixedPlanner(EditorialPlan(selections=(EditorialSelection("traced"),)))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with selection_trace.tracing() as recorded:
        pipeline.run_selection([candidate])

    assert planner.seen_trace is recorded


def test_editorial_render_decision_survives_without_mutating_the_source_clip(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("freeze-frame", duration=8.0), 1.0, 8.0, 0.8)
    decision = EditorialSelection(
        "freeze-frame",
        start_time=2.0,
        end_time=6.0,
        render_mode="still",
        render_frame_seconds=4.25,
    )
    planner = _FixedPlanner(EditorialPlan(selections=(decision,)))
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    result = pipeline.run_selection([candidate])

    assert result.selected_clips[0] is candidate.clip
    assert (candidate.start_time, candidate.end_time) == (1.0, 8.0)
    assert result.clip_segments == {"freeze-frame": (2.0, 6.0)}
    assert result.editorial_selections == (decision,)


def test_editorial_timing_override_must_resolve_to_a_forward_segment(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("bad-timing"), 1.0, 8.0, 0.8)
    planner = _FixedPlanner(
        EditorialPlan(selections=(EditorialSelection("bad-timing", start_time=9.0),))
    )
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,
    )

    with pytest.raises(ValueError, match="invalid source timing.*bad-timing"):
        pipeline.run_selection([candidate])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"asset_id": " "},
        {"asset_id": "asset", "start_time": float("nan")},
        {"asset_id": "asset", "end_time": float("inf")},
        {"asset_id": "asset", "start_time": -0.1},
        {"asset_id": "asset", "start_time": 3.0, "end_time": 3.0},
        {"asset_id": "asset", "render_frame_seconds": 1.0},
        {
            "asset_id": "asset",
            "render_mode": "motion",
            "render_frame_seconds": 1.0,
        },
        {"asset_id": "asset", "render_mode": "animated"},
    ],
)
def test_editorial_selection_rejects_invalid_runtime_contracts(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        EditorialSelection(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selections": [EditorialSelection("asset")]},
        {"unavailable_reason": " "},
        {
            "selections": (EditorialSelection("asset"),),
            "unavailable_reason": "provider down",
        },
    ],
)
def test_editorial_plan_rejects_ambiguous_or_mutable_runtime_contracts(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        EditorialPlan(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("candidate", "decision"),
    [
        (
            ClipWithSegment(make_clip("nan-source"), float("nan"), 3.0, 0.8),
            EditorialSelection("nan-source"),
        ),
        (
            ClipWithSegment(make_clip("past-source", duration=5.0), 0.0, 7.0, 0.8),
            EditorialSelection("past-source"),
        ),
        (
            ClipWithSegment(make_clip("bad-frame", duration=5.0), 0.0, 4.0, 0.8),
            EditorialSelection(
                "bad-frame",
                render_mode="still",
                render_frame_seconds=6.0,
            ),
        ),
        (
            ClipWithSegment(make_clip("eof-frame", duration=5.0), 0.0, 4.0, 0.8),
            EditorialSelection(
                "eof-frame",
                render_mode="still",
                render_frame_seconds=5.0,
            ),
        ),
    ],
)
def test_editorial_plan_rejects_non_finite_or_out_of_source_render_timing(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
    candidate: ClipWithSegment,
    decision: EditorialSelection,
) -> None:
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=_FixedPlanner(EditorialPlan(selections=(decision,))),
    )

    with pytest.raises(ValueError, match=candidate.clip.asset.id):
        pipeline.run_selection([candidate])


def test_editorial_plan_records_the_terminal_cut_for_asset_stories(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    kept = ClipWithSegment(make_clip("kept"), 0.0, 3.0, 0.8)
    dropped = ClipWithSegment(make_clip("dropped"), 0.0, 3.0, 0.7)
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=_FixedPlanner(EditorialPlan(selections=(EditorialSelection("kept"),))),
    )

    with selection_trace.tracing() as recorded:
        pipeline.run_selection([kept, dropped])

    assert recorded.stages[-1].name == "editorial final cut"
    assert recorded.story_of("kept").shipped is True
    assert recorded.story_of("dropped").dropped_at == "editorial final cut"


def test_editorial_planner_analyzes_selected_fallback_then_replans_without_legacy_editor(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    fallback = ClipWithSegment(
        make_clip("unseen", duration=5.0),
        0.0,
        5.0,
        0.42,
        analyzed=False,
    )
    planner = _ReplanningPlanner()
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,  # type: ignore[arg-type]
    )
    analyzer = _RecordingAnalyzer()
    pipeline.analyzer = analyzer  # type: ignore[assignment]
    pipeline.quality.analyzer = analyzer  # type: ignore[assignment]
    pipeline.quality.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]

    result = pipeline.run_selection([fallback], verify=True)

    assert len(planner.calls) == 2
    assert planner.calls[0][0] is fallback
    assert planner.analyzed_states == [(False,), (True,)]
    assert planner.calls[1][0].clip is fallback.clip
    assert planner.calls[1][0].analyzed is True
    assert analyzer.calls == [[fallback.clip]]
    assert analyzer.closed == 1
    assert result.selected_clips == [fallback.clip]
    assert result.clip_segments == {"unseen": (1.0, 4.0)}


def test_failed_editorial_verification_keeps_fallback_measurement_and_does_not_loop(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    fallback = ClipWithSegment(
        make_clip("decode-failed", duration=5.0),
        0.5,
        4.5,
        0.72,
        analyzed=False,
    )
    planner = _ReplanningPlanner()
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,  # type: ignore[arg-type]
    )
    analyzer = _FailedAnalyzer()
    pipeline.quality.analyzer = analyzer  # type: ignore[assignment]
    pipeline.quality.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]

    result = pipeline.run_selection([fallback], verify=True)

    assert planner.analyzed_states == [(False,), (False,)]
    assert analyzer.calls == [[fallback.clip]]
    assert analyzer.closed == 1
    assert (fallback.start_time, fallback.end_time, fallback.score) == (0.5, 4.5, 0.72)
    assert fallback.analyzed is False
    assert result.clip_segments == {"decode-failed": (0.5, 4.5)}


def test_editorial_photo_verification_uses_photo_look_and_replans_same_wrapper(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    clip = make_clip("unseen-photo", duration=5.0)
    clip.asset.type = AssetType.IMAGE
    fallback = ClipWithSegment(clip, 0.0, 5.0, 0.31, analyzed=False)
    planner = _ReplanningPlanner()
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=planner,  # type: ignore[arg-type]
    )
    pipeline.quality.analyzer = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.quality.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    pipeline.refiner = _ForbiddenLegacyStage()  # type: ignore[assignment]
    looked_at: list[ClipWithSegment] = []

    def look(stills, **_kwargs) -> None:
        looked_at.extend(stills)
        stills[0].score = 0.88
        stills[0].clip.llm_description = "A family crosses the finish line."

    # WHY: looking at stills is a vision-model call; the fake scores one picture instead.
    with patch(
        "immich_memories.analysis.photo_look.look_at_stills",
        side_effect=look,
    ):
        result = pipeline.run_selection([fallback], verify=True)

    assert looked_at == [fallback]
    assert planner.calls[0][0] is planner.calls[1][0] is fallback
    assert fallback.score == 0.88
    assert result.selected_clips == [clip]


def test_editorial_planner_reports_the_refining_phase_before_completion(
    mock_immich_client,
    mock_analysis_cache,
    mock_thumbnail_cache,
) -> None:
    candidate = ClipWithSegment(make_clip("visible-progress"), 0.0, 3.0, 0.8)
    pipeline = _pipeline(
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
        planner=_FixedPlanner(EditorialPlan(selections=(EditorialSelection("visible-progress"),))),
    )
    updates: list[dict] = []

    pipeline.run_selection([candidate], progress_callback=updates.append)

    assert "Refining Final Selection" in [update["phase_label"] for update in updates]
    assert updates[-1]["phase_label"] == "Complete"
