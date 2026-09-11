"""Regression tests for Step 2's initial eligibility selection."""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.ui.pages.step2_loading import _set_initial_selection
from immich_memories.ui.state import AppState


def _asset(asset_id: str, asset_type: AssetType = AssetType.VIDEO) -> Asset:
    when = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )


def _regular_clip(asset_id: str) -> VideoClipInfo:
    return VideoClipInfo(asset=_asset(asset_id), duration_seconds=14.3)


def _live_photo_clip(asset_id: str) -> VideoClipInfo:
    asset = _asset(asset_id, AssetType.IMAGE).model_copy(
        update={"live_photo_video_id": f"{asset_id}-video"}
    )
    return VideoClipInfo(asset=asset, duration_seconds=1.5)


def test_initial_selection_keeps_live_photos_eligible_when_video_sources_look_long() -> None:
    """Regression: raw source length must not pre-delete the 29 Live Photos."""
    regular = [_regular_clip(f"video-{index}") for index in range(32)]
    live = [_live_photo_clip(f"live-{index}") for index in range(29)]
    state = AppState(
        include_live_photos=True,
        duration_mode="auto",
        target_duration=2.5,
    )

    _set_initial_selection([*regular, *live], state)

    assert len(state.selected_clip_ids) == 61
    assert {clip.asset.id for clip in live} <= state.selected_clip_ids
