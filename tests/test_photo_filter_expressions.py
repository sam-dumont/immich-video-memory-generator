"""Tests for photo animation FFmpeg filter expressions."""

from __future__ import annotations

import pytest

from immich_memories.photos.filter_expressions import (
    blur_bg_filter,
    collage_filter,
    face_zoom_filter,
    ken_burns_filter,
)


class TestKenBurnsFilter:
    """Tests for Ken Burns (zoom + pan) filter generation."""

    def test_returns_zoompan_filter(self):
        """Ken Burns filter contains a zoompan expression."""
        result = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
        )
        assert "zoompan=" in result

    def test_zoom_factor_in_expression(self):
        """Zoom endpoint matches the requested factor."""
        result = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
        )
        assert "1.15" in result

    def test_output_resolution(self):
        """Output size is set to target dimensions."""
        result = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
        )
        assert "s=1920x1080" in result

    def test_frame_count_matches_duration(self):
        """Total frames = fps * duration."""
        result = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
        )
        # d=120 (30fps * 4s)
        assert "d=120" in result

    def test_face_center_shifts_pan(self):
        """When face_center is provided, x/y expressions shift toward face."""
        result_no_face = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
            seed=42,
        )
        result_with_face = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
            face_center=(0.7, 0.3),
            seed=42,
        )
        assert result_no_face != result_with_face

    def test_includes_scale_before_zoompan(self):
        """Source image is scaled up before zoompan for quality."""
        result = ken_burns_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            zoom_factor=1.15,
        )
        assert "scale=" in result

    def test_seed_produces_reproducible_output(self):
        """Same seed produces identical filter strings."""
        args = {
            "width": 4000,
            "height": 3000,
            "target_w": 1920,
            "target_h": 1080,
            "duration": 4.0,
            "fps": 30,
            "zoom_factor": 1.15,
            "seed": 123,
        }
        assert ken_burns_filter(**args) == ken_burns_filter(**args)

    def test_different_seeds_produce_different_pans(self):
        """Different seeds produce different pan directions."""
        args = {
            "width": 4000,
            "height": 3000,
            "target_w": 1920,
            "target_h": 1080,
            "duration": 4.0,
            "fps": 30,
            "zoom_factor": 1.15,
        }
        results = {ken_burns_filter(**args, seed=i) for i in range(20)}
        # With 9 pan directions, 20 seeds should hit at least 3
        assert len(results) >= 3


class TestFaceZoomFilter:
    """Tests for face zoom (crop to face bbox + gentle zoom) filter."""

    def test_contains_crop(self):
        """Face zoom crops to the face bounding box region."""
        result = face_zoom_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            face_bbox=(0.3, 0.2, 0.4, 0.5),
        )
        assert "crop=" in result

    def test_contains_zoompan(self):
        """Face zoom includes a gentle zoom effect after crop."""
        result = face_zoom_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            face_bbox=(0.3, 0.2, 0.4, 0.5),
        )
        assert "zoompan=" in result

    def test_output_resolution(self):
        """Output matches target dimensions."""
        result = face_zoom_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            face_bbox=(0.3, 0.2, 0.4, 0.5),
        )
        assert "s=1920x1080" in result

    def test_crop_stays_within_bounds(self):
        """Crop coordinates are clamped to image bounds even for edge faces."""
        result = face_zoom_filter(
            width=4000,
            height=3000,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
            face_bbox=(0.0, 0.0, 0.1, 0.1),
        )
        assert "crop=" in result
        # Crop params should all be non-negative integers
        crop_part = result.split("crop=")[1].split(",")[0]
        values = [int(v) for v in crop_part.split(":")]
        assert all(v >= 0 for v in values)

    def test_seed_varies_zoom_direction(self):
        """Different seeds produce zoom-in vs zoom-out variants."""
        args = {
            "width": 4000,
            "height": 3000,
            "target_w": 1920,
            "target_h": 1080,
            "duration": 4.0,
            "fps": 30,
            "face_bbox": (0.3, 0.2, 0.4, 0.5),
        }
        results = {face_zoom_filter(**args, seed=i) for i in range(20)}
        # Should get both zoom-in and zoom-out variants
        assert len(results) >= 2


class TestBlurBgFilter:
    """Tests for blur background filter (portrait in landscape frame)."""

    def test_contains_boxblur(self):
        """Blur background uses heavy gaussian-like blur."""
        result = blur_bg_filter(
            width=1080,
            height=1920,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
        )
        assert "boxblur=" in result

    def test_contains_overlay(self):
        """Foreground is overlaid centered on blurred background."""
        result = blur_bg_filter(
            width=1080,
            height=1920,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
        )
        assert "overlay=" in result

    def test_contains_zoompan(self):
        """Adds subtle zoom on the foreground for life."""
        result = blur_bg_filter(
            width=1080,
            height=1920,
            target_w=1920,
            target_h=1080,
            duration=4.0,
            fps=30,
        )
        assert "zoompan=" in result

    def test_seed_varies_zoom_amount(self):
        """Different seeds produce different zoom amounts."""
        args = {
            "width": 1080,
            "height": 1920,
            "target_w": 1920,
            "target_h": 1080,
            "duration": 4.0,
            "fps": 30,
        }
        results = {blur_bg_filter(**args, seed=i) for i in range(20)}
        assert len(results) >= 3


class TestCollageFilter:
    """Tests for multi-photo collage (Apple-style slide-in stack)."""

    def test_two_photos(self):
        """Collage with 2 photos creates overlay chain."""
        result = collage_filter(
            photos=[(1920, 1080), (1920, 1080)],
            target_w=1920,
            target_h=1080,
            duration=6.0,
            fps=30,
        )
        assert "overlay=" in result
        assert "[p0]" in result
        assert "[p1]" in result

    def test_three_photos(self):
        """Collage with 3 photos creates 3 overlay stages."""
        result = collage_filter(
            photos=[(1920, 1080), (1920, 1080), (1920, 1080)],
            target_w=1920,
            target_h=1080,
            duration=6.0,
            fps=30,
        )
        assert result.count("overlay=") == 3

    def test_rejects_fewer_than_2(self):
        """Collage requires at least 2 photos."""
        with pytest.raises(ValueError, match="2-4"):
            collage_filter(
                photos=[(1920, 1080)],
                target_w=1920,
                target_h=1080,
                duration=6.0,
                fps=30,
            )

    def test_rejects_more_than_4(self):
        """Collage requires at most 4 photos."""
        with pytest.raises(ValueError, match="2-4"):
            collage_filter(
                photos=[(1920, 1080)] * 5,
                target_w=1920,
                target_h=1080,
                duration=6.0,
                fps=30,
            )

    def test_creates_black_base(self):
        """Collage starts with a black background canvas."""
        result = collage_filter(
            photos=[(1920, 1080), (1920, 1080)],
            target_w=1920,
            target_h=1080,
            duration=6.0,
            fps=30,
        )
        assert "color=c=black" in result

    def test_seed_varies_slide_direction(self):
        """Different seeds produce different slide directions."""
        args = {
            "photos": [(1920, 1080), (1920, 1080)],
            "target_w": 1920,
            "target_h": 1080,
            "duration": 6.0,
            "fps": 30,
        }
        results = {collage_filter(**args, seed=i) for i in range(20)}
        # 4 slide directions, should hit at least 2 in 20 seeds
        assert len(results) >= 2
