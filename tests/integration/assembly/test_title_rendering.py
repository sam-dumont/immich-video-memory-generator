"""Integration tests for title rendering — PIL and FFmpeg renderers.

These tests verify actual image/video output. PIL tests are always run.
FFmpeg tests require ffmpeg installed (skipped otherwise).

Run: make test-integration-assembly
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from immich_memories.titles.styles import TitleStyle
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]

# ---------------------------------------------------------------------------
# PIL Renderer tests
# ---------------------------------------------------------------------------


class TestPILTitleRenderer:
    """Tests for PIL-based title screen rendering."""

    def _make_renderer(self, **settings_kwargs):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        defaults = {
            "width": 640,
            "height": 360,
            "fps": 30.0,
            "duration": 1.0,
            "animation_duration": 0.3,
        }
        defaults.update(settings_kwargs)
        settings = RenderSettings(**defaults)
        style = TitleStyle(
            background_colors=["#FFF5E6", "#FFE4CC"],
            animation_preset="fade_up",
        )
        return TitleRenderer(style, settings)

    def test_render_frame_returns_pil_image(self):
        from PIL import Image

        renderer = self._make_renderer()
        frame = renderer.render_frame("Hello World")
        assert isinstance(frame, Image.Image)
        assert frame.size == (640, 360)
        assert frame.mode == "RGB"

    def test_render_frame_with_subtitle(self):
        from PIL import Image

        renderer = self._make_renderer()
        frame = renderer.render_frame("Title", subtitle="Subtitle")
        assert isinstance(frame, Image.Image)
        assert frame.size == (640, 360)

    def test_render_frame_is_not_blank(self):
        renderer = self._make_renderer()
        frame = renderer.render_frame("Test Title", frame_number=15)
        arr = np.array(frame)
        # Should have some non-uniform pixels (text was drawn)
        assert arr.std() > 0, "Frame appears blank (uniform color)"

    def test_render_all_frames_count(self):
        renderer = self._make_renderer()
        frames = renderer.render_all_frames("All Frames Test", fade_out_duration=0.3)
        expected = int(1.0 * 30.0)  # duration * fps
        assert len(frames) == expected

    def test_render_all_frames_parallel(self):
        renderer = self._make_renderer(
            duration=2.0,  # Need enough frames to trigger parallel path
        )
        frames = renderer.render_all_frames_parallel(
            "Parallel Test", max_workers=2, fade_out_duration=0.5
        )
        expected = int(2.0 * 30.0)
        assert len(frames) == expected

    def test_render_all_frames_parallel_small_count_falls_back(self):
        """With very few frames, parallel render falls back to sequential."""
        renderer = self._make_renderer(duration=0.1)
        frames = renderer.render_all_frames_parallel("Tiny", max_workers=8, fade_out_duration=0.05)
        expected = int(0.1 * 30.0)
        assert len(frames) == expected

    def test_text_transform_uppercase(self):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        style = TitleStyle(
            text_transform="uppercase",
            background_colors=["#FFFFFF"],
        )
        settings = RenderSettings(width=320, height=240, fps=1, duration=0.1)
        renderer = TitleRenderer(style, settings)
        # Internal method test — _apply_text_transform
        assert renderer._apply_text_transform("hello") == "HELLO"

    def test_text_transform_capitalize(self):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        style = TitleStyle(text_transform="capitalize")
        settings = RenderSettings(width=320, height=240, fps=1, duration=0.1)
        renderer = TitleRenderer(style, settings)
        assert renderer._apply_text_transform("hello world") == "Hello World"

    def test_text_transform_none(self):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        style = TitleStyle(text_transform="none")
        settings = RenderSettings(width=320, height=240, fps=1, duration=0.1)
        renderer = TitleRenderer(style, settings)
        assert renderer._apply_text_transform("Hello") == "Hello"


class TestPILColorParsing:
    """Test color parsing helper methods."""

    def _make_renderer(self):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        return TitleRenderer(
            TitleStyle(), RenderSettings(width=100, height=100, fps=1, duration=0.1)
        )

    def test_parse_hex_color_6_digit(self):
        renderer = self._make_renderer()
        assert renderer._parse_color("#FF8800") == (255, 136, 0)

    def test_parse_hex_color_3_digit(self):
        renderer = self._make_renderer()
        assert renderer._parse_color("#F80") == (255, 136, 0)

    def test_parse_color_with_alpha(self):
        renderer = self._make_renderer()
        assert renderer._parse_color_with_alpha("#FF0000", 0.5) == (255, 0, 0, 127)

    def test_luminance_white(self):
        renderer = self._make_renderer()
        assert renderer._calculate_luminance("#FFFFFF") == pytest.approx(1.0)

    def test_luminance_black(self):
        renderer = self._make_renderer()
        assert renderer._calculate_luminance("#000000") == pytest.approx(0.0)


class TestOptimalTextSettings:
    """Test automatic text color selection based on background."""

    def _renderer_with_bg(self, colors: list[str]):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        style = TitleStyle(background_colors=colors)
        return TitleRenderer(style, RenderSettings(width=100, height=100, fps=1, duration=0.1))

    def test_dark_background_gets_light_text(self):
        renderer = self._renderer_with_bg(["#111111", "#222222"])
        text_color, _ = renderer._get_optimal_text_settings()
        r, g, b = renderer._parse_color(text_color)
        # Light text for dark backgrounds
        assert r > 200

    def test_light_background_gets_dark_text(self):
        renderer = self._renderer_with_bg(["#FFFFFF", "#EEEEEE"])
        text_color, _ = renderer._get_optimal_text_settings()
        r, g, b = renderer._parse_color(text_color)
        # Dark text for light backgrounds
        assert r < 100

    def test_settings_cached(self):
        renderer = self._renderer_with_bg(["#888888"])
        first = renderer._get_optimal_text_settings()
        second = renderer._get_optimal_text_settings()
        assert first == second


class TestBlendModes:
    """Test PIL blend layer modes."""

    def _make_renderer(self):
        from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer

        return TitleRenderer(
            TitleStyle(), RenderSettings(width=100, height=100, fps=1, duration=0.1)
        )

    def _make_layers(self):
        from PIL import Image

        base = Image.new("RGBA", (10, 10), (128, 128, 128, 255))
        top = Image.new("RGBA", (10, 10), (200, 200, 200, 128))
        return base, top

    def test_normal_blend(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "normal")
        assert isinstance(result, Image.Image)

    def test_multiply_blend(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "multiply")
        assert isinstance(result, Image.Image)

    def test_screen_blend(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "screen")
        assert isinstance(result, Image.Image)

    def test_overlay_blend(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "overlay")
        assert isinstance(result, Image.Image)

    def test_soft_light_blend(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "soft_light")
        assert isinstance(result, Image.Image)

    def test_unknown_blend_falls_back_to_normal(self):
        from PIL import Image

        renderer = self._make_renderer()
        base, top = self._make_layers()
        result = renderer._blend_layers(base, top, "unknown_mode")
        assert isinstance(result, Image.Image)


class TestRenderTitleFrame:
    """Test the module-level render_title_frame convenience function."""

    def test_returns_numpy_array(self):
        from immich_memories.titles.renderer_pil import render_title_frame

        style = TitleStyle(background_colors=["#AABBCC"])
        arr = render_title_frame("Title", None, style, 320, 240, 0.5)
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (240, 320, 3)

    def test_with_subtitle(self):
        from immich_memories.titles.renderer_pil import render_title_frame

        style = TitleStyle()
        arr = render_title_frame("Title", "Sub", style, 320, 240, 0.0)
        assert arr.shape == (240, 320, 3)


# ---------------------------------------------------------------------------
# FFmpeg Renderer tests
# ---------------------------------------------------------------------------


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not installed")
class TestFFmpegEscaping:
    """Test FFmpeg text escaping."""

    def test_escape_colon(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        assert "\\:" in _escape_ffmpeg_text("Title: Subtitle")

    def test_escape_percent(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        assert "\\%" in _escape_ffmpeg_text("100%")

    def test_escape_brackets(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        result = _escape_ffmpeg_text("[test]")
        assert "\\[" in result
        assert "\\]" in result

    def test_escape_semicolon(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        assert "\\;" in _escape_ffmpeg_text("a;b")

    def test_escape_backslash(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        assert "\\\\" in _escape_ffmpeg_text("path\\file")

    def test_strips_control_characters(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        result = _escape_ffmpeg_text("Hello\x00World\x07!")
        assert "\x00" not in result
        assert "\x07" not in result
        assert "HelloWorld!" in result.replace("\\", "")

    def test_preserves_spaces(self):
        from immich_memories.titles.renderer_ffmpeg import _escape_ffmpeg_text

        result = _escape_ffmpeg_text("Hello World")
        assert "Hello World" in result


@pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not installed")
class TestFFmpegTitleGeneration:
    """Integration tests for FFmpeg title screen generation.

    Uses create_title_with_effects (filter_complex) which is the primary
    code path. create_title_ffmpeg uses -vf which has escaping issues
    with some FFmpeg versions.
    """

    def test_create_title_with_effects(self, tmp_path: Path):
        from immich_memories.titles.renderer_ffmpeg import (
            FFmpegTitleConfig,
            create_title_with_effects,
        )

        output = tmp_path / "title_fx.mp4"
        config = FFmpegTitleConfig(width=320, height=240, fps=30.0, duration=1.5)
        result = create_title_with_effects("Fancy Title", "With Effects", output, config)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_create_title_with_effects_no_subtitle(self, tmp_path: Path):
        from immich_memories.titles.renderer_ffmpeg import (
            FFmpegTitleConfig,
            create_title_with_effects,
        )

        output = tmp_path / "title_fx_nosub.mp4"
        config = FFmpegTitleConfig(width=320, height=240, fps=30.0, duration=1.5)
        result = create_title_with_effects("No Subtitle", None, output, config)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_output_is_valid_video(self, tmp_path: Path):
        """Verify the output is a valid video using ffprobe."""
        from immich_memories.titles.renderer_ffmpeg import (
            FFmpegTitleConfig,
            create_title_with_effects,
        )

        output = tmp_path / "valid.mp4"
        config = FFmpegTitleConfig(width=320, height=240, fps=30.0, duration=1.5)
        create_title_with_effects("Valid?", None, output, config)

        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0
        assert "320" in probe.stdout
        assert "240" in probe.stdout

    def test_creates_parent_directory(self, tmp_path: Path):
        from immich_memories.titles.renderer_ffmpeg import (
            FFmpegTitleConfig,
            create_title_with_effects,
        )

        output = tmp_path / "subdir" / "nested" / "title.mp4"
        config = FFmpegTitleConfig(width=320, height=240, fps=30.0, duration=1.5)
        result = create_title_with_effects("Nested", None, output, config)
        assert result.exists()

    def test_special_characters_in_title(self, tmp_path: Path):
        from immich_memories.titles.renderer_ffmpeg import (
            FFmpegTitleConfig,
            create_title_with_effects,
        )

        output = tmp_path / "special.mp4"
        config = FFmpegTitleConfig(width=320, height=240, fps=30.0, duration=1.5)
        result = create_title_with_effects("Title: 100% Special", None, output, config)
        assert result.exists()


@pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not installed")
class TestFFmpegTitleConfig:
    def test_defaults(self):
        from immich_memories.titles.renderer_ffmpeg import FFmpegTitleConfig

        cfg = FFmpegTitleConfig()
        assert cfg.width == 1920
        assert cfg.height == 1080
        assert cfg.fps == 30.0
        assert cfg.duration == 3.5
        assert cfg.fade_in_duration == 0.6
        assert cfg.fade_out_duration == 1.0

    def test_custom_config(self):
        from immich_memories.titles.renderer_ffmpeg import FFmpegTitleConfig

        cfg = FFmpegTitleConfig(width=3840, height=2160, bg_color1="000000")
        assert cfg.width == 3840
        assert cfg.bg_color1 == "000000"
