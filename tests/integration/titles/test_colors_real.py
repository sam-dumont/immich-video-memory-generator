"""Real color extraction and conversion integration tests.

Tests color space roundtrips, brightness math, and FFmpeg-based
keyframe extraction / dominant color detection from real video.

Run: make test-integration-titles
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from immich_memories.titles.colors import (
    brighten_color,
    create_color_fade_frames,
    extract_dominant_color,
    extract_keyframes_from_video,
    get_brightness,
    hex_to_rgb,
    hsl_to_rgb,
    rgb_to_hex,
    rgb_to_hsl,
)
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration]


# ---- Pure color math (no FFmpeg needed) ----


class TestColorConversions:
    def test_hex_rgb_roundtrip(self):
        original = "#3A7BF2"
        rgb = hex_to_rgb(original)
        assert rgb_to_hex(rgb) == original

    def test_hex_rgb_short_form(self):
        assert hex_to_rgb("#F80") == (255, 136, 0)

    def test_rgb_hsl_roundtrip(self):
        """RGB → HSL → RGB should be within ±1 due to float rounding."""
        original = (120, 200, 50)
        hsl = rgb_to_hsl(original)
        back = hsl_to_rgb(hsl)
        for orig, got in zip(original, back, strict=True):
            assert abs(orig - got) <= 1, f"Roundtrip drift: {original} → {hsl} → {back}"


class TestBrightness:
    def test_black_is_zero(self):
        assert get_brightness((0, 0, 0)) == 0.0

    def test_white_is_255(self):
        assert get_brightness((255, 255, 255)) == pytest.approx(255.0, abs=0.1)

    def test_mid_gray(self):
        b = get_brightness((128, 128, 128))
        assert 100 < b < 160

    def test_brighten_increases(self):
        dark = (40, 60, 80)
        bright = brighten_color(dark, factor=2.0)
        assert get_brightness(bright) > get_brightness(dark)


class TestColorFadeFrames:
    def test_frame_count(self):
        frames = create_color_fade_frames((255, 0, 0), (0, 0, 255), 10, 64, 64)
        assert len(frames) == 10

    def test_first_frame_is_start_color(self):
        frames = create_color_fade_frames((255, 0, 0), (0, 0, 255), 10, 64, 64)
        first_arr = np.array(frames[0])
        mean_r = first_arr[:, :, 0].mean()
        mean_b = first_arr[:, :, 2].mean()
        assert mean_r > 200, "First frame should be mostly red"
        assert mean_b < 50

    def test_last_frame_is_end_color(self):
        frames = create_color_fade_frames((255, 0, 0), (0, 0, 255), 10, 64, 64)
        last_arr = np.array(frames[-1])
        mean_r = last_arr[:, :, 0].mean()
        mean_b = last_arr[:, :, 2].mean()
        assert mean_b > 200, "Last frame should be mostly blue"
        assert mean_r < 50


# ---- FFmpeg-dependent tests (real video extraction) ----


@requires_ffmpeg
class TestKeyframeExtraction:
    def test_extracts_pil_images(self, test_clip_720p: Path):
        frames = extract_keyframes_from_video(test_clip_720p, count=3)
        assert len(frames) == 3
        from PIL import Image

        for f in frames:
            assert isinstance(f, Image.Image)
            assert f.size[0] > 0 and f.size[1] > 0

    def test_extracted_frames_have_content(self, test_clip_720p: Path):
        """Keyframes from testsrc2 should not be blank."""
        frames = extract_keyframes_from_video(test_clip_720p, count=2)
        for f in frames:
            arr = np.array(f)
            assert arr.std() > 5, "Extracted keyframe appears blank"


@requires_ffmpeg
class TestDominantColor:
    def test_returns_rgb_tuple(self, test_clip_720p: Path):
        color = extract_dominant_color([test_clip_720p])
        assert isinstance(color, tuple)
        assert len(color) == 3
        assert all(0 <= c <= 255 for c in color)

    def test_meets_brightness_floor(self, test_clip_720p: Path):
        """Dominant color enforces minimum brightness ≥ 100."""
        color = extract_dominant_color([test_clip_720p])
        assert get_brightness(color) >= 100
