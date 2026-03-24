"""Pixel-level integration tests for TitleScreenGenerator.

Verifies that generated title videos have correct visual properties:
dark backgrounds, visible text, working fades, correct resolution.

Uses PIL fallback (no GPU) at 720p rendered, 320x180 extracted for speed.
Run: make test-integration-titles
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
from immich_memories.titles.styles import TitleStyle
from tests.integration.conftest import requires_ffmpeg
from tests.integration.titles.conftest import (
    TITLE_FPS,
    TITLE_H,
    TITLE_W,
    extract_frame_rgb,
    ffprobe_stream,
    has_audio_stream,
    region_mean,
    region_std,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg]

# Fixed style for deterministic tests — dark cinematic, no randomness
TEST_STYLE = TitleStyle(
    name="test_dark",
    background_colors=["#1A1A2E", "#16213E"],
    background_type="soft_gradient",
    text_color="#FFFFFF",
    animation_preset="fade_up",
    font_weight="semibold",
)


def _make_generator(tmp_path: Path, **config_overrides) -> TitleScreenGenerator:
    defaults = {
        "use_gpu_rendering": False,
        "orientation": "landscape",
        "resolution": "720p",
        "fps": float(TITLE_FPS),
        "title_duration": 1.0,
        "month_divider_duration": 0.5,
        "ending_duration": 1.0,
        "animated_background": False,
        "hdr": False,
    }
    defaults.update(config_overrides)
    config = TitleScreenConfig(**defaults)
    return TitleScreenGenerator(config=config, style=TEST_STYLE, output_dir=tmp_path)


class TestTitleScreenPixels:
    """Pixel-level assertions on generated title screen videos."""

    def test_background_is_dark(self, tmp_path):
        """Title screen corners should be dark (cinematic background)."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_title_screen(year=2024)

        # WHY: Extract mid-video frame (past fade-in). Check corners which
        # should be pure background (no text overlay).
        frame = extract_frame_rgb(screen.path, 5, TITLE_W, TITLE_H)
        top_left = region_mean(frame, 0.0, 0.1, 0.0, 0.1)
        bottom_right = region_mean(frame, 0.9, 1.0, 0.9, 1.0)

        # WHY: Dark cinematic backgrounds should have mean < 80.
        # If background is accidentally white/bright, this catches it.
        assert top_left < 80, f"Top-left corner too bright: {top_left}"
        assert bottom_right < 80, f"Bottom-right corner too bright: {bottom_right}"

    def test_text_is_visible(self, tmp_path):
        """Center band (where text renders) should have brighter pixels than corners."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_title_screen(year=2024)

        frame = extract_frame_rgb(screen.path, 5, TITLE_W, TITLE_H)
        h, w = frame.shape[:2]

        # WHY: White text on dark background creates bright pixels in the
        # center band (30-70% height). The corner has only gradient pixels.
        # Pixel range (max - min) is much larger in center due to text.
        center = frame[int(h * 0.3) : int(h * 0.7), :]
        corner = frame[: int(h * 0.1), : int(w * 0.1)]
        center_range = int(center.max()) - int(center.min())
        corner_range = int(corner.max()) - int(corner.min())

        assert center_range > corner_range, (
            f"Center pixel range ({center_range}) should exceed corner range "
            f"({corner_range}) — text may not be rendering"
        )

    def test_fade_from_white_first_frame_bright(self, tmp_path):
        """First frame should be near-white (fade-from-white start)."""
        # WHY: Need title_duration > 1.8s so fade-in frames (0.8s) aren't
        # consumed by the fade-out region (last 1.0s of the video).
        gen = _make_generator(tmp_path, title_duration=2.5)
        screen = gen.generate_title_screen(year=2024)

        first_frame = extract_frame_rgb(screen.path, 0, TITLE_W, TITLE_H)
        mean_brightness = float(first_frame.mean())

        # WHY: fade_from_white=True means frame 0 should be nearly white.
        # If fade is broken, first frame will be dark background.
        assert mean_brightness > 180, (
            f"First frame mean brightness {mean_brightness:.0f} — expected >180 for fade-from-white"
        )

    def test_mid_video_darker_than_start(self, tmp_path):
        """Mid-video frame should be darker than first frame (fade completed)."""
        # WHY: Need enough duration so fade-in and mid frames are distinct
        # from the fade-out region that occupies the final 1.0s.
        gen = _make_generator(tmp_path, title_duration=2.5)
        screen = gen.generate_title_screen(year=2024)

        first = extract_frame_rgb(screen.path, 0, TITLE_W, TITLE_H)
        # WHY: At 10fps, fade-in is 8 frames (0.8s). Frame 12 is past
        # fade-in but before fade-out starts at frame 15 (total=25, out=25-10=15).
        mid = extract_frame_rgb(screen.path, 12, TITLE_W, TITLE_H)

        first_mean = float(first.mean())
        mid_mean = float(mid.mean())

        # WHY: After fade-from-white completes, background should be visible
        # (dark). If fade isn't progressive, both frames would be similar.
        assert mid_mean < first_mean - 50, (
            f"Mid-video ({mid_mean:.0f}) should be significantly darker than "
            f"first frame ({first_mean:.0f}) — fade-from-white may be broken"
        )

    def test_has_audio_stream(self, tmp_path):
        """Title video must have audio (required for assembly concat)."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_title_screen(year=2024)

        assert has_audio_stream(screen.path), (
            "Title video missing audio stream — assembly will crash during concat"
        )

    def test_correct_resolution(self, tmp_path):
        """Output resolution matches config."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_title_screen(year=2024)

        stream = ffprobe_stream(screen.path)
        # WHY: 720p landscape = 1280x720. Video renders at this native
        # resolution; pixel tests rescale to 320x180 on extraction only.
        assert int(stream["width"]) == 1280
        assert int(stream["height"]) == 720

    def test_portrait_resolution(self, tmp_path):
        """Portrait config produces portrait output."""
        gen = _make_generator(tmp_path, orientation="portrait")
        screen = gen.generate_title_screen(year=2024)

        stream = ffprobe_stream(screen.path)
        w, h = int(stream["width"]), int(stream["height"])
        assert h > w, f"Portrait should be taller than wide: {w}x{h}"


class TestMonthDividerPixels:
    def test_divider_has_text(self, tmp_path):
        """Month divider should render month name text."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_month_divider(month=6)

        frame = extract_frame_rgb(screen.path, 3, TITLE_W, TITLE_H)
        center_std = region_std(frame, 0.3, 0.7)

        # WHY: If text rendering fails, the frame is uniform background.
        assert center_std > 5, (
            f"Divider center std {center_std:.1f} too low — month text may not be rendering"
        )

    def test_divider_background_dark(self, tmp_path):
        """Divider background should match dark cinematic style."""
        gen = _make_generator(tmp_path)
        screen = gen.generate_month_divider(month=6)

        frame = extract_frame_rgb(screen.path, 3, TITLE_W, TITLE_H)
        corner = region_mean(frame, 0.0, 0.1, 0.0, 0.1)
        assert corner < 80, f"Divider corner too bright: {corner}"


class TestAllScreensPixels:
    def test_ending_differs_from_title(self, tmp_path):
        """Ending should lack bright text pixels that title has."""
        gen = _make_generator(tmp_path, title_duration=2.0, ending_duration=2.0)
        screens = gen.generate_all_screens(
            year=2024,
            months_in_video=[1, 6],
        )

        # WHY: Use mid-video frames past fade-in but before fade-out.
        # At 10fps with 2s duration: fade_in=8 frames, fade_out starts at frame 10.
        # Frame 9 is the sweet spot (fully revealed, not yet fading out).
        title_frame = extract_frame_rgb(screens["title"].path, 9, TITLE_W, TITLE_H)
        ending_frame = extract_frame_rgb(screens["ending"].path, 3, TITLE_W, TITLE_H)

        # WHY: Title has white text (max near 255). Ending is pure background
        # fade (no text), so max should be much lower. This catches a bug where
        # ending accidentally renders text or title renders no text.
        title_max = int(title_frame.max())
        ending_max = int(ending_frame.max())

        assert title_max > 200, f"Title should have bright text pixels (max={title_max})"
        assert ending_max < title_max - 50, (
            f"Ending max ({ending_max}) should be much dimmer than title max ({title_max}) "
            f"— ending may be rendering text when it shouldn't"
        )

    def test_sdr_no_hdr_metadata(self, tmp_path):
        """SDR title should not have HDR color metadata."""
        gen = _make_generator(tmp_path, hdr=False)
        screen = gen.generate_title_screen(year=2024)

        stream = ffprobe_stream(screen.path)
        color_trc = stream.get("color_transfer", "")

        # WHY: SDR title tagged as HDR causes display issues on SDR monitors.
        assert color_trc != "arib-std-b67", f"SDR title has HDR transfer function '{color_trc}'"
