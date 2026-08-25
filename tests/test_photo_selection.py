"""What survived the two-budget chain: how photos reach generation.

The scoring-plus-budget function this file was named for is gone. Photographs
and video now compete in one pool, so what is left to check is the wiring
either side of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from immich_memories.api.models import Asset


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

    def test_build_generation_params_disables_photo_path(self):
        """Photos are in selected_clips as IMAGE assets. The old _add_photos_if_enabled
        path must be disabled to avoid double-adding them."""
        state = MagicMock()
        state.generation_options = {}
        state.selected_person = None
        state.date_range = None
        state.include_photos = True
        state.photo_assets = [_make_asset("p1")]
        state.photo_duration = 4.0
        state.config = MagicMock()
        state.config.photos.duration = 4.0
        state.immich_url = "http://localhost:2283"
        state.immich_api_key = "test-key"
        state.demo_mode = False
        state.memory_type = None
        state.memory_preset_params = {}
        state.title_suggestion_title = None
        state.title_suggestion_subtitle = None
        state.clip_segments = {}
        state.clip_rotations = {}
        state.target_duration = 10
        state.selected_photo_ids = {"p1"}

        with patch("immich_memories.api.immich.SyncImmichClient"):
            from immich_memories.ui.pages._step4_generate import _build_generation_params

            params = _build_generation_params(state, [], MagicMock())

        # Unified pool: photos already in selected_clips, old path disabled
        assert params.include_photos is False
        assert params.photo_assets is None
        assert params.selected_photo_ids is None
