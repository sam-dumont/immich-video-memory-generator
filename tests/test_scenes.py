"""Tests for scene detection."""

from __future__ import annotations

import pytest

try:
    import cv2  # noqa: F401
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)

from immich_memories.analysis.scenes import Scene


class TestScene:
    """Tests for Scene dataclass."""

    def test_contains_time(self):
        """Boundary behavior: both start and end are inclusive."""
        scene = Scene(
            start_time=10.0,
            end_time=20.0,
            start_frame=300,
            end_frame=600,
        )
        assert scene.contains_time(15.0)
        assert scene.contains_time(10.0)
        assert scene.contains_time(20.0)
        assert not scene.contains_time(9.9)
        assert not scene.contains_time(20.1)

    def test_scene_with_zero_duration(self):
        """A scene where start_time == end_time has zero duration and only contains that exact time."""
        scene = Scene(
            start_time=5.0,
            end_time=5.0,
            start_frame=150,
            end_frame=150,
        )
        assert scene.duration == 0.0
        assert scene.contains_time(5.0)
        assert not scene.contains_time(4.999)
        assert not scene.contains_time(5.001)
