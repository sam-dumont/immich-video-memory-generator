"""Integration tests for title rendering — the PIL renderer.

These tests verify actual image output.

Run: make test-integration-titles
"""

from __future__ import annotations

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
