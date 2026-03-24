"""Tests for scaling_utilities — FFmpeg filter strings and mood aggregation."""

from __future__ import annotations

from types import SimpleNamespace

from immich_memories.processing.scaling_utilities import (
    _get_aspect_ratio_filter,
    _get_smart_crop_filter,
    aggregate_mood_from_clips,
)

# ---------------------------------------------------------------------------
# TestGetAspectRatioFilter
# ---------------------------------------------------------------------------


class TestGetAspectRatioFilter:
    """Verify FFmpeg filter strings for each scale mode."""

    def test_similar_aspect_ratio_simple_scale(self):
        """When src and target AR differ < 5%, use simple scale (no crop/blur)."""
        result = _get_aspect_ratio_filter(0, 1920, 1080, 1920, 1080, None)
        assert "scale=1920:1080" in result
        assert "boxblur" not in result
        assert "crop=" not in result
        assert "pad=" not in result

    def test_portrait_in_landscape_blur_mode(self):
        """Portrait (1080x1920) in landscape (1920x1080) with blur background."""
        result = _get_aspect_ratio_filter(0, 1080, 1920, 1920, 1080, None, scale_mode="blur")
        assert "boxblur" in result
        assert "overlay" in result
        assert "split" in result
        # Background fills target dimensions
        assert "scale=1920:1080" in result

    def test_landscape_in_portrait_blur_mode(self):
        """Landscape in portrait target with blur background."""
        result = _get_aspect_ratio_filter(0, 1920, 1080, 1080, 1920, None, scale_mode="blur")
        assert "boxblur" in result
        assert "overlay" in result

    def test_black_bars_mode(self):
        """Black bars mode pads with black, no blur."""
        result = _get_aspect_ratio_filter(0, 1080, 1920, 1920, 1080, None, scale_mode="black_bars")
        assert "pad=1920:1080" in result
        assert "boxblur" not in result
        assert "black" in result

    def test_smart_zoom_with_face(self):
        """Smart zoom with face center produces crop filter."""
        result = _get_aspect_ratio_filter(
            0, 1080, 1920, 1920, 1080, (0.5, 0.3), scale_mode="smart_zoom"
        )
        assert "crop=" in result
        assert "boxblur" not in result
        # Scales to target after crop
        assert "scale=1920:1080" in result

    def test_smart_zoom_without_face_falls_back_to_blur(self):
        """Smart zoom with no face falls back to blur mode."""
        result = _get_aspect_ratio_filter(0, 1080, 1920, 1920, 1080, None, scale_mode="smart_zoom")
        assert "boxblur" in result
        # The blur path uses crop= for background fill, but there's no
        # face-centered _get_smart_crop_filter output (which has crop=W:H:X:Y
        # with 4 colon-separated values for positioning)
        assert "split" in result  # blur mode splits into bg/fg

    def test_clip_index_in_labels(self):
        """Clip index appears in FFmpeg stream labels."""
        result = _get_aspect_ratio_filter(5, 1080, 1920, 1920, 1080, None)
        # Blur mode uses labels like bg5, fg5, blurred5, scaled5
        assert "bg5" in result
        assert "fg5" in result
        assert "[v5scaled]" in result

    def test_rotation_filter_prepended(self):
        """Rotation filter is prepended to the pipeline."""
        result = _get_aspect_ratio_filter(
            0, 1920, 1080, 1920, 1080, None, rotation_filter="transpose=1,"
        )
        assert "transpose=1," in result

    def test_hdr_conversion_included(self):
        """HDR conversion filter is included in the pipeline."""
        result = _get_aspect_ratio_filter(
            0, 1920, 1080, 1920, 1080, None, hdr_conversion=",zscale=t=bt709"
        )
        assert "zscale=t=bt709" in result

    def test_fps_and_format_in_output(self):
        """fps, format, and setsar are always in the filter."""
        result = _get_aspect_ratio_filter(
            0, 1920, 1080, 1920, 1080, None, pix_fmt="yuv420p", target_fps=30
        )
        assert "fps=30" in result
        assert "format=yuv420p" in result
        assert "setsar=1" in result

    def test_output_suffix_in_label(self):
        """Custom output suffix appears in the output label."""
        result = _get_aspect_ratio_filter(0, 1080, 1920, 1920, 1080, None, output_suffix="custom")
        assert "[v0custom]" in result

    def test_default_blur_mode(self):
        """Default scale mode is blur when AR differs significantly."""
        result = _get_aspect_ratio_filter(0, 1080, 1920, 1920, 1080, None)
        assert "boxblur" in result

    def test_colorspace_filter_included(self):
        """Colorspace filter appears in common suffix."""
        result = _get_aspect_ratio_filter(
            0, 1920, 1080, 1920, 1080, None, colorspace_filter=",colorspace=bt709"
        )
        assert "colorspace=bt709" in result


# ---------------------------------------------------------------------------
# TestGetSmartCropFilter
# ---------------------------------------------------------------------------


class TestGetSmartCropFilter:
    """Verify crop coordinate calculation for face-centered zoom."""

    @staticmethod
    def _parse_crop(result: str) -> tuple[int, int, int, int]:
        """Extract crop=W:H:X:Y values from filter string."""
        crop_part = result.split(",")[0]  # "crop=W:H:X:Y"
        _, dims = crop_part.split("=")
        w, h, x, y = (int(v) for v in dims.split(":"))
        return w, h, x, y

    def test_center_face_center_crop(self):
        """Face at center (0.5, 0.5) with landscape src -> portrait target crops width."""
        result = _get_smart_crop_filter(1920, 1080, 1080, 1920, 0.5, 0.5)
        crop_w, crop_h, _, _ = self._parse_crop(result)
        # src_ar (1.78) > target_ar (0.5625) -> source is wider -> crop width
        assert crop_w < 1920
        assert crop_h == 1080
        assert "scale=1080:1920" in result

    def test_face_at_left_edge_clamped(self):
        """Face at left edge (0.0, 0.5) clamps crop_x to 0."""
        result = _get_smart_crop_filter(1920, 1080, 1080, 1920, 0.0, 0.5)
        _, _, crop_x, _ = self._parse_crop(result)
        assert crop_x == 0

    def test_face_at_right_edge_clamped(self):
        """Face at right edge (1.0, 0.5) clamps crop_x to max valid position."""
        result = _get_smart_crop_filter(1920, 1080, 1080, 1920, 1.0, 0.5)
        crop_w, _, crop_x, _ = self._parse_crop(result)
        assert crop_x + crop_w <= 1920

    def test_face_at_top_edge_clamped(self):
        """Face at top (0.5, 0.0) clamps crop_y to 0."""
        # Portrait src -> landscape target: src_ar (0.56) < target_ar (1.78) -> crop height
        result = _get_smart_crop_filter(1080, 1920, 1920, 1080, 0.5, 0.0)
        _, _, _, crop_y = self._parse_crop(result)
        assert crop_y == 0

    def test_face_at_bottom_edge_clamped(self):
        """Face at bottom (0.5, 1.0) clamps crop_y to max valid position."""
        result = _get_smart_crop_filter(1080, 1920, 1920, 1080, 0.5, 1.0)
        _, crop_h, _, crop_y = self._parse_crop(result)
        assert crop_y + crop_h <= 1920

    def test_output_includes_scale(self):
        """Output always scales to target resolution after crop."""
        result = _get_smart_crop_filter(1920, 1080, 3840, 2160, 0.5, 0.5)
        assert "scale=3840:2160" in result

    def test_crop_preserves_target_aspect_ratio(self):
        """Crop dimensions match the target aspect ratio."""
        result = _get_smart_crop_filter(1920, 1080, 1080, 1920, 0.5, 0.5)
        crop_w, crop_h, _, _ = self._parse_crop(result)
        crop_ar = crop_w / crop_h
        target_ar = 1080 / 1920
        assert abs(crop_ar - target_ar) < 0.02, (
            f"Crop AR {crop_ar:.3f} doesn't match target AR {target_ar:.3f}"
        )

    def test_taller_source_crops_height(self):
        """When source is taller than target AR, height is cropped."""
        result = _get_smart_crop_filter(1080, 1920, 1920, 1080, 0.5, 0.5)
        crop_w, crop_h, _, _ = self._parse_crop(result)
        # src_ar (0.5625) < target_ar (1.778) -> crop height
        assert crop_w == 1080
        assert crop_h < 1920


# ---------------------------------------------------------------------------
# TestAggregateMood
# ---------------------------------------------------------------------------


class TestAggregateMood:
    """Verify mood aggregation from clip emotions."""

    @staticmethod
    def _make_clip(emotion: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(llm_emotion=emotion)

    def test_empty_clips_returns_none(self):
        assert aggregate_mood_from_clips([]) is None

    def test_no_emotions_returns_none(self):
        clips = [self._make_clip(None), self._make_clip(None)]
        assert aggregate_mood_from_clips(clips) is None

    def test_single_emotion_returned(self):
        clips = [self._make_clip("happy")]
        assert aggregate_mood_from_clips(clips) == "happy"

    def test_dominant_mood_wins(self):
        clips = [
            self._make_clip("happy"),
            self._make_clip("happy"),
            self._make_clip("calm"),
        ]
        assert aggregate_mood_from_clips(clips) == "happy"

    def test_emotion_mapped_to_mood(self):
        """'joyful' maps to 'happy' mood category."""
        clips = [self._make_clip("joyful")]
        assert aggregate_mood_from_clips(clips) == "happy"

    def test_case_insensitive(self):
        clips = [self._make_clip("HAPPY")]
        assert aggregate_mood_from_clips(clips) == "happy"

    def test_unknown_emotion_passed_through(self):
        """Unknown emotions are used as-is (not mapped)."""
        clips = [self._make_clip("mysterious")]
        assert aggregate_mood_from_clips(clips) == "mysterious"

    def test_mixed_mapped_emotions(self):
        """Multiple emotions in the same family all map to one mood."""
        clips = [
            self._make_clip("joyful"),
            self._make_clip("cheerful"),
            self._make_clip("calm"),
        ]
        # joyful->happy, cheerful->happy, calm->calm -> happy wins (2 vs 1)
        assert aggregate_mood_from_clips(clips) == "happy"

    def test_empty_string_emotion_ignored(self):
        """Empty string emotions are falsy and should be skipped."""
        clips = [self._make_clip(""), self._make_clip("calm")]
        assert aggregate_mood_from_clips(clips) == "calm"

    def test_all_mood_families_mapped(self):
        """Spot-check one member from each mood family."""
        families = {
            "delighted": "happy",
            "serene": "calm",
            "excited": "energetic",
            "fun": "playful",
            "sentimental": "nostalgic",
            "loving": "romantic",
            "quiet": "peaceful",
            "thrilling": "exciting",
        }
        for emotion, expected_mood in families.items():
            clips = [self._make_clip(emotion)]
            assert aggregate_mood_from_clips(clips) == expected_mood, (
                f"{emotion} should map to {expected_mood}"
            )
