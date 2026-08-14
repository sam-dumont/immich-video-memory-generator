"""UI pipeline boundary tests: reviewed assets are the authoritative pool."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from immich_memories.analysis.smart_pipeline import PipelineConfig
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.ui.pages.clip_pipeline import (
    _eligible_pipeline_media,
    _resolve_auto_duration_for_selection,
    _run_pipeline_blocking,
)
from immich_memories.ui.state import AppState


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


def test_blocking_pipeline_cannot_reintroduce_unchecked_photos() -> None:
    selected_photo = _photo("keep-photo")
    unchecked_photo = _photo("drop-photo")
    state = AppState(
        config=Config(),
        immich_url="http://immich.test",
        immich_api_key="test-key",
        include_photos=True,
        photo_assets=[selected_photo, unchecked_photo],
        thumbnail_cache=MagicMock(),
        analysis_cache=MagicMock(),
    )
    selection_result = MagicMock(
        selected_clips=[],
        clip_segments={},
        errors=[],
        stats={},
    )
    pipeline = MagicMock()
    pipeline.run_analysis.return_value = []
    pipeline.run_selection.return_value = selection_result
    progress_state = {"cancelled": False, "done": False, "error": None}

    with (
        patch("immich_memories.ui.pages.clip_pipeline.SyncImmichClient") as client_cls,
        patch("immich_memories.analysis.smart_pipeline.SmartPipeline", return_value=pipeline),
        patch("immich_memories.config.get_config", return_value=state.config),
        patch(
            "immich_memories.cli._pipeline_runner._merge_photos_into_pool",
            return_value=[],
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
    assert merge_photos.call_args.kwargs["photo_assets"] == [selected_photo]
