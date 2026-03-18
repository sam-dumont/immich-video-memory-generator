"""Tests for photo support models."""

from __future__ import annotations

from pathlib import Path

from immich_memories.photos import AnimationMode, PhotoClipInfo, PhotoGroup


class TestAnimationMode:
    """Tests for AnimationMode enum."""

    def test_enum_values(self):
        """All animation modes have string values matching their lowercase names."""
        assert AnimationMode.KEN_BURNS == "ken_burns"
        assert AnimationMode.FACE_ZOOM == "face_zoom"
        assert AnimationMode.BLUR_BG == "blur_bg"
        assert AnimationMode.COLLAGE == "collage"
        assert AnimationMode.AUTO == "auto"

    def test_enum_is_str(self):
        """AnimationMode values are strings (StrEnum)."""
        assert isinstance(AnimationMode.KEN_BURNS, str)


class TestPhotoClipInfo:
    """Tests for PhotoClipInfo dataclass."""

    def test_basic_construction(self):
        """PhotoClipInfo stores path, dimensions, duration, and animation mode."""
        clip = PhotoClipInfo(
            asset_id="abc-123",
            source_path=Path("/tmp/photo.jpg"),
            output_path=Path("/tmp/photo.mp4"),
            width=1920,
            height=1080,
            duration=4.0,
            animation_mode=AnimationMode.KEN_BURNS,
            score=0.75,
        )
        assert clip.asset_id == "abc-123"
        assert clip.width == 1920
        assert clip.height == 1080
        assert clip.duration == 4.0
        assert clip.animation_mode == AnimationMode.KEN_BURNS
        assert clip.score == 0.75

    def test_optional_fields_default_to_none(self):
        """Face bbox and GPS fields default to None."""
        clip = PhotoClipInfo(
            asset_id="x",
            source_path=Path("/tmp/a.jpg"),
            output_path=Path("/tmp/a.mp4"),
            width=100,
            height=100,
            duration=3.0,
            animation_mode=AnimationMode.AUTO,
            score=0.5,
        )
        assert clip.face_bbox is None
        assert clip.latitude is None
        assert clip.longitude is None
        assert clip.date is None

    def test_is_landscape(self):
        """is_landscape is True when width > height."""
        clip = PhotoClipInfo(
            asset_id="l",
            source_path=Path("/tmp/l.jpg"),
            output_path=Path("/tmp/l.mp4"),
            width=1920,
            height=1080,
            duration=4.0,
            animation_mode=AnimationMode.KEN_BURNS,
            score=0.5,
        )
        assert clip.is_landscape is True

    def test_is_portrait(self):
        """is_landscape is False when height >= width."""
        clip = PhotoClipInfo(
            asset_id="p",
            source_path=Path("/tmp/p.jpg"),
            output_path=Path("/tmp/p.mp4"),
            width=1080,
            height=1920,
            duration=4.0,
            animation_mode=AnimationMode.BLUR_BG,
            score=0.5,
        )
        assert clip.is_landscape is False


class TestPhotoGroup:
    """Tests for PhotoGroup dataclass."""

    def test_single_photo_group(self):
        """A group with one photo is a single, not a series."""
        group = PhotoGroup(
            asset_ids=["a1"],
            animation_mode=AnimationMode.KEN_BURNS,
        )
        assert group.is_series is False
        assert len(group.asset_ids) == 1

    def test_multi_photo_series(self):
        """A group with 2+ photos is a series."""
        group = PhotoGroup(
            asset_ids=["a1", "a2", "a3"],
            animation_mode=AnimationMode.COLLAGE,
        )
        assert group.is_series is True
        assert len(group.asset_ids) == 3
