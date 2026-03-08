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

    def test_duration(self):
        """Test duration calculation."""
        scene = Scene(
            start_time=10.0,
            end_time=25.0,
            start_frame=300,
            end_frame=750,
        )
        assert scene.duration == 15.0

    def test_midpoint(self):
        """Test midpoint calculation."""
        scene = Scene(
            start_time=10.0,
            end_time=30.0,
            start_frame=300,
            end_frame=900,
        )
        assert scene.midpoint == 20.0

    def test_contains_time(self):
        """Test time containment check."""
        scene = Scene(
            start_time=10.0,
            end_time=20.0,
            start_frame=300,
            end_frame=600,
        )
        assert scene.contains_time(15.0) is True
        assert scene.contains_time(10.0) is True
        assert scene.contains_time(20.0) is True
        assert scene.contains_time(9.9) is False
        assert scene.contains_time(20.1) is False

    def test_to_dict(self):
        """Test dictionary conversion."""
        scene = Scene(
            start_time=5.0,
            end_time=10.0,
            start_frame=150,
            end_frame=300,
            keyframe_path="/path/to/keyframe.jpg",
        )
        d = scene.to_dict()
        assert d["start_time"] == 5.0
        assert d["end_time"] == 10.0
        assert d["start_frame"] == 150
        assert d["end_frame"] == 300
        assert d["keyframe_path"] == "/path/to/keyframe.jpg"
