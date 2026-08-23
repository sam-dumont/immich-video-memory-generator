"""Behavioral tests for photos/animator.py — photo source preparation."""

from __future__ import annotations

import pytest

from immich_memories.photos.animator import prepare_photo_source
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


class TestPreparePhotoSource:
    def test_jpeg_returns_path_and_dimensions(self, test_photo_landscape, tmp_path):
        """JPEG input should return a PreparedPhoto with correct dimensions."""
        result = prepare_photo_source(test_photo_landscape, tmp_path)

        assert result.path.exists()
        assert result.width == 1920
        assert result.height == 1080
        assert result.has_gain_map is False
