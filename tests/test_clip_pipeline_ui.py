"""UI pipeline boundary tests: reviewed assets are the authoritative pool."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.analysis.selection_coverage import AnalysisCoverage
from immich_memories.analysis.smart_pipeline import PipelineConfig, PipelineResult
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from immich_memories.ui.pages.clip_pipeline import (
    _build_pipeline_config,
    _configure_timeline_for_selection,
    _eligible_pipeline_media,
    _pipeline_summary_counts,
    _resolve_auto_duration_for_selection,
    _run_pipeline_blocking,
)
from immich_memories.ui.state import AppState

_WINDOW = DateRange(datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 31, tzinfo=UTC))


def _source_pipeline(result):
    pipeline = MagicMock()
    pipeline.last_deep_analysis_count = 0
    pipeline.run_editorial_source.return_value = (result.selected_clips, result)
    pipeline.run_analysis.side_effect = AssertionError("UI must use the editorial source route")
    pipeline.run_selection.side_effect = AssertionError("UI must use the editorial source route")
    return pipeline


def _photo(asset_id: str, *, day: int = 1) -> Asset:
    when = datetime(2026, 7, day, 9, 0, tzinfo=UTC)
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )


def _clip(asset_id: str, *, duration: float = 5.0) -> VideoClipInfo:
    when = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )
    return VideoClipInfo(asset=asset, duration_seconds=duration)


def test_eligible_pipeline_media_uses_only_reviewed_clips_and_photos() -> None:
    clips = [_clip("keep-video"), _clip("drop-video")]
    photos = [_photo("keep-photo"), _photo("drop-photo")]
    state = AppState(
        include_photos=True,
        photo_assets=photos,
        selected_clip_ids={"keep-video"},
        selected_photo_ids={"keep-photo"},
    )

    eligible_clips, eligible_photos = _eligible_pipeline_media(state, clips)

    assert [clip.asset.id for clip in eligible_clips] == ["keep-video"]
    assert [photo.id for photo in eligible_photos] == ["keep-photo"]


def test_trip_auto_duration_is_resolved_from_reviewed_media_only() -> None:
    selected_clip = _clip("keep-video")
    selected_photo = _photo("keep-photo")
    state = AppState(
        config=Config(),
        memory_type="trip",
        duration_mode="auto",
        include_photos=True,
        target_duration=7.0,
    )

    result = _resolve_auto_duration_for_selection(state, [selected_clip], [selected_photo])

    assert result is not None
    assert result.total_seconds == 15.0
    assert state.target_duration_seconds == 15.0


def test_selection_uses_the_persisted_timeline_content_budget() -> None:
    clips = [_clip(f"video-{day}") for day in range(1, 13)]
    photos = [_photo(f"photo-{day}", day=day) for day in range(1, 13)]
    state = AppState(
        config=Config(),
        memory_type="trip",
        duration_mode="manual",
        target_duration=2.5,
        hdr_only=True,
    )

    plan = _configure_timeline_for_selection(state, clips, photos)
    pipeline_config = _build_pipeline_config(state, clips)

    assert plan.target_duration == 150.0
    assert state.timeline_plan is plan
    assert pipeline_config.target_duration_seconds == plan.content_budget
    # The one pool switch the editorial route still reads travels with the plan.
    assert pipeline_config.hdr_only is True


def test_pipeline_summary_distinguishes_eligible_deep_and_planned_counts() -> None:
    result = {
        "stats": {
            "eligible_count": 61,
            "deeply_analyzed_count": 24,
            "planned_count": 30,
            "total_analyzed": 60,
            "selected_count": 30,
        }
    }

    assert _pipeline_summary_counts(result) == (61, 24, 30)


def test_blocking_pipeline_cannot_reintroduce_unchecked_photos() -> None:
    selected_photo = _photo("keep-photo")
    unchecked_photo = _photo("drop-photo")
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        include_photos=True,
        photo_assets=[selected_photo, unchecked_photo],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = PipelineResult(
        selected_clips=[],
        clip_segments={},
        errors=[],
        stats={},
    )
    pipeline = _source_pipeline(selection_result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary the blocking pipeline call reaches.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is a stand-in; the test reads what run_editorial_source got.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.config.get_config", return_value=state.config),
        patch(
            "immich_memories.cli._candidate_pool._merge_photos_into_pool",
            side_effect=AssertionError("Source selection must not use the legacy photo merge"),
        ) as merge_photos,
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(
            state,
            PipelineConfig(),
            [],
            [selected_photo],
            progress_state,
        )

    assert progress_state["error"] is None
    merge_photos.assert_not_called()
    assert pipeline.run_editorial_source.call_args.args[0] == [selected_photo]
    assert state.pipeline_result is not None
    assert state.pipeline_result["stats"]["eligible_count"] == 1
    assert state.pipeline_result["stats"]["deeply_analyzed_count"] == 0
    assert state.pipeline_result["stats"]["planned_count"] == 0


def test_blocking_pipeline_retains_exact_ordered_editorial_carriers_and_decisions() -> None:
    planned_photo = VideoClipInfo(asset=_photo("planned-photo"), duration_seconds=4.0)
    planned_video = _clip("planned-video")
    selected_clips = [planned_photo, planned_video]
    decisions = (
        EditorialSelection(asset_id="planned-photo", render_mode="still"),
        EditorialSelection(asset_id="planned-video", render_mode="motion"),
    )
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        clips=[_clip("library-only")],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = PipelineResult(
        selected_clips=selected_clips,
        editorial_selections=decisions,
        clip_segments={"planned-photo": (0.0, 4.0), "planned-video": (0.0, 5.0)},
        errors=[],
        stats={},
        coverage=AnalysisCoverage(analyzed=2, total=2),
    )
    pipeline = _source_pipeline(selection_result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary the blocking pipeline call reaches.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is a stand-in; the test checks the carriers it hands back.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.config.get_config", return_value=state.config),
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [], [], progress_state)

    assert progress_state["error"] is None
    assert state.pipeline_selected_clips is selected_clips
    assert state.editorial_selections is decisions
    assert state.get_selected_clips() == selected_clips
    assert state.get_selected_clips()[0] is planned_photo


def test_blocking_pipeline_hands_source_selection_its_thumbnail_cache() -> None:
    """The production source builder retains the preview cache used by its judgments."""
    photo = _photo("burst-frame")
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        include_photos=True,
        photo_assets=[photo],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = PipelineResult(selected_clips=[], clip_segments={}, errors=[], stats={})
    pipeline = _source_pipeline(selection_result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary — the wizard's library read.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is not under test here; only what its builder is handed.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ) as build_pipeline,
        patch("immich_memories.config.get_config", return_value=state.config),
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [], [photo], progress_state)

    assert progress_state["error"] is None
    assert build_pipeline.call_args.kwargs["thumbnail_cache"] is state.thumbnail_cache
    assert pipeline.run_editorial_source.call_args.args[0] == [photo]


class _RecordingLock:
    """Counts how often the worker takes the session lock for its write-back."""

    def __init__(self) -> None:
        self.entered = 0

    def __enter__(self) -> None:
        self.entered += 1

    def __exit__(self, *exc: object) -> None:
        return None


def test_blocking_pipeline_writes_its_result_back_under_the_session_lock() -> None:
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    lock = _RecordingLock()
    state.lock = lock  # type: ignore[assignment]
    pipeline = _source_pipeline(
        PipelineResult(selected_clips=[], clip_segments={}, errors=[], stats={})
    )
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary — the wizard's library read.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is not under test; only how its result reaches the session.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.config.get_config", return_value=state.config),
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [], [], progress_state)

    assert progress_state["error"] is None
    assert lock.entered == 1
    assert state.pipeline_running is False


def test_blocking_pipeline_remembers_where_the_cut_wrote_its_plan(tmp_path) -> None:
    """The story page reads the plan from the attempt directory the route reports."""
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = PipelineResult(
        selected_clips=[],
        clip_segments={},
        errors=[],
        stats={"editorial_attempt_directory": str(tmp_path / "attempts" / "one")},
    )
    pipeline = _source_pipeline(selection_result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary — the wizard's library read.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is not under test; only where it says the plan went.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.config.get_config", return_value=state.config),
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [], [], progress_state)

    assert progress_state["error"] is None
    assert state.editorial_attempt_dir == tmp_path / "attempts" / "one"

    state.reset_clips()

    assert state.editorial_attempt_dir is None


def test_blocking_pipeline_hands_the_review_page_its_pool_coverage() -> None:
    """The review step warns when the pool was mostly metadata guesses (#489).

    It can only do that if the count survives the hop from the worker thread to
    `state.pipeline_result`, the plain dict the page reads back.
    """
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        date_ranges=[_WINDOW],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = PipelineResult(
        selected_clips=[],
        clip_segments={},
        errors=[],
        stats={},
        coverage=AnalysisCoverage(analyzed=25, total=149),
    )
    pipeline = _source_pipeline(selection_result)
    progress_state = {"cancelled": False, "done": False, "error": None}

    # WHY: get_config would read the developer's own config.yaml off disk.
    with (
        # WHY: Immich is the external boundary — the wizard's library read.
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        # WHY: the pipeline is not under test; only what it hands the page.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.config.get_config", return_value=state.config),
    ):
        client_cls.return_value.__enter__.return_value = MagicMock()
        _run_pipeline_blocking(state, PipelineConfig(), [], [], progress_state)

    assert progress_state["error"] is None
    assert state.pipeline_result is not None
    assert state.pipeline_result["coverage"] == AnalysisCoverage(analyzed=25, total=149)
