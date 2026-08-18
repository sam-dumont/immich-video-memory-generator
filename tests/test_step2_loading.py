"""Regression tests for Step 2's initial eligibility selection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import immich_memories.ui.pages.step2_loading as step2_loading
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.cache.versions import ANALYSIS_VERSION
from immich_memories.config_loader import Config
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


def test_load_surfaces_only_current_model_cached_analysis() -> None:
    current = _regular_clip("current")
    stale = _regular_clip("stale")
    current_segment = MagicMock(
        start_time=1.0,
        end_time=6.0,
        total_score=0.92,
        llm_description="A child running into the sea",
        llm_emotion="excited",
        llm_setting="beach",
        llm_subjects=["child"],
        llm_interestingness=0.9,
        llm_quality=0.86,
        audio_categories=["waves"],
    )
    current_analysis = MagicMock(
        model_version="qwen-3.6", segments=[current_segment], analysis_version=ANALYSIS_VERSION
    )
    current_analysis.get_best_segment.return_value = current_segment
    stale_analysis = MagicMock(model_version="qwen-3.5", segments=[MagicMock()])
    cache = MagicMock()
    cache.get_analysis.side_effect = {
        "current": current_analysis,
        "stale": stale_analysis,
    }.get
    state = AppState(
        config=Config(
            llm={"model": "qwen-3.6"},
            content_analysis={"enabled": True},
        ),
        analysis_cache=cache,
    )

    hydrated = step2_loading._hydrate_compatible_cached_analysis(state, [current, stale])

    assert hydrated == 1
    assert state.cached_analysis_ids == {"current"}
    assert state.clip_segments == {"current": (1.0, 6.0)}
    assert current.llm_description == "A child running into the sea"
    assert current.llm_emotion == "excited"
    assert current.llm_setting == "beach"
    assert current.llm_subjects == ["child"]
    assert current.llm_interestingness == 0.9
    assert current.llm_quality == 0.86
    assert current.audio_categories == ["waves"]
    assert stale.llm_description is None
