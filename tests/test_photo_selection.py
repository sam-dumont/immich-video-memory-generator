"""What survived the two-budget chain: how photos reach generation.

The scoring-plus-budget function this file was named for is gone. Photographs
and video now compete in one pool, so what is left to check is the wiring
either side of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.ui.state import AppState


def _make_asset(asset_id: str, favorite: bool = False) -> Asset:
    now = datetime.now(tz=UTC)
    return Asset(
        id=asset_id,
        type="IMAGE",
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=favorite,
        # A camera original names its camera. Selection drops stills that do
        # not, because on a real library those are what arrived through a
        # messaging app rather than what anybody shot.
        exifInfo={"make": "Apple", "model": "iPhone 15 Pro"},
    )


class TestAppStatePhotoFields:
    """What the wizard remembers about photos.

    scored_photos and photo_budget_result went with the two-budget chain: both
    were only ever written empty, and nothing ever read either.
    """

    def test_reset_clips_clears_the_photo_selection(self):
        from immich_memories.ui.state import AppState

        state = AppState()
        state.selected_photo_ids = {"p1", "p2"}

        state.reset_clips()

        assert state.selected_photo_ids == set()


class TestStep4PassesPreSelectedPhotos:
    """Step 4 disables old photo path — photos are in selected_clips via unified pool."""

    def test_build_generation_params_disables_photo_path(self, tmp_path: Path):
        """Photos are in selected_clips as IMAGE assets. The old _add_photos_if_enabled
        path must be disabled to avoid double-adding them."""
        photo = _make_asset("p1")
        selected = [VideoClipInfo(asset=photo, width=1920, height=1080, duration_seconds=4.0)]
        state = AppState(
            config=Config(),
            include_photos=True,
            photo_assets=[photo],
            photo_duration=4.0,
            immich_url="http://localhost:2283",
            immich_api_key="test-key",
            selected_photo_ids={"p1"},
        )

        # WHY: constructing the Immich client is the external connection boundary.
        with patch("immich_memories.api.immich.SyncImmichClient"):
            from immich_memories.ui.pages._step4_generate import _build_generation_params

            params = _build_generation_params(state, selected, tmp_path / "memory.mp4")

        # Unified pool: photos already in selected_clips, old path disabled
        assert params.clips == selected
        assert params.include_photos is False
        assert params.photo_assets is None
        assert params.selected_photo_ids is None
