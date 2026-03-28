"""Behavior tests for titles, audio, and miscellaneous modules.

Covers uncovered branches in:
- titles: generator, colors, fonts, backgrounds, rendering_service, convenience
- audio: mixer_helpers, mood_analyzer_backends
- misc: filename_builder, search_service
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module 1: Titles — colors.py
# ---------------------------------------------------------------------------


class TestColorsHSLConversions:
    """Behavior tests for HSL color space conversions."""

    def test_rgb_to_hsl_pure_red(self):
        from immich_memories.titles.colors import rgb_to_hsl

        h, s, lightness = rgb_to_hsl((255, 0, 0))
        assert abs(h) < 1 or abs(h - 360) < 1  # hue ~0 or ~360
        assert s == pytest.approx(100, abs=1)
        assert lightness == pytest.approx(50, abs=1)

    def test_rgb_to_hsl_white(self):
        from immich_memories.titles.colors import rgb_to_hsl

        _, _, lightness = rgb_to_hsl((255, 255, 255))
        assert lightness == pytest.approx(100, abs=1)

    def test_hsl_to_rgb_roundtrip(self):
        from immich_memories.titles.colors import hsl_to_rgb, rgb_to_hsl

        original = (128, 64, 200)
        hsl = rgb_to_hsl(original)
        recovered = hsl_to_rgb(hsl)
        for i in range(3):
            assert abs(recovered[i] - original[i]) <= 2  # rounding tolerance

    def test_hsl_to_rgb_black(self):
        from immich_memories.titles.colors import hsl_to_rgb

        result = hsl_to_rgb((0, 0, 0))
        assert result == (0, 0, 0)


class TestGetBrightness:
    """Behavior tests for perceived brightness calculation."""

    def test_white_is_brightest(self):
        from immich_memories.titles.colors import get_brightness

        assert get_brightness((255, 255, 255)) == pytest.approx(255, abs=1)

    def test_black_is_darkest(self):
        from immich_memories.titles.colors import get_brightness

        assert get_brightness((0, 0, 0)) == pytest.approx(0, abs=0.1)

    def test_green_is_brightest_primary(self):
        """Green contributes most to perceived brightness (0.587 weight)."""
        from immich_memories.titles.colors import get_brightness

        assert get_brightness((0, 255, 0)) > get_brightness((255, 0, 0))
        assert get_brightness((0, 255, 0)) > get_brightness((0, 0, 255))


class TestHexShorthand:
    """Test 3-char hex shorthand expansion."""

    def test_short_hex_expanded(self):
        from immich_memories.titles.colors import hex_to_rgb

        assert hex_to_rgb("#FFF") == (255, 255, 255)
        assert hex_to_rgb("#000") == (0, 0, 0)
        assert hex_to_rgb("ABC") == (170, 187, 204)


class TestEnsureMinimumBrightness:
    """Test minimum brightness enforcement preserves already-bright colors."""

    def test_bright_color_unchanged(self):
        from immich_memories.titles.colors import ensure_minimum_brightness

        bright = (200, 200, 200)
        result = ensure_minimum_brightness(bright, min_brightness=100)
        assert result == bright

    def test_very_dark_color_brightened(self):
        from immich_memories.titles.colors import ensure_minimum_brightness, get_brightness

        dark = (10, 10, 10)
        result = ensure_minimum_brightness(dark, min_brightness=100)
        assert get_brightness(result) > get_brightness(dark)

    def test_black_color_brightened_with_capped_factor(self):
        """Factor is capped at 3.0 for very dark colors (near zero brightness)."""
        from immich_memories.titles.colors import ensure_minimum_brightness

        result = ensure_minimum_brightness((1, 1, 1), min_brightness=200)
        assert all(v >= 0 for v in result)


class TestQuantizeColors:
    """Test color quantization behavior."""

    def test_empty_list_returns_empty(self):
        from immich_memories.titles.colors import quantize_colors

        assert quantize_colors([]) == []

    def test_reduces_to_target_clusters(self):
        from immich_memories.titles.colors import quantize_colors

        many_colors = [(i, i, i) for i in range(0, 256, 10)]
        result = quantize_colors(many_colors, num_clusters=4)
        assert len(result) <= 4

    def test_similar_colors_merge(self):
        from immich_memories.titles.colors import quantize_colors

        similar = [(100, 100, 100), (101, 101, 101), (102, 102, 102)]
        result = quantize_colors(similar, num_clusters=8)
        assert len(result) == 1


class TestExtractColorsFromImage:
    """Test color extraction from PIL images."""

    def test_solid_color_image(self):
        from PIL import Image

        from immich_memories.titles.colors import extract_colors_from_image

        img = Image.new("RGB", (100, 100), (255, 0, 0))
        colors = extract_colors_from_image(img, num_colors=3)
        assert len(colors) >= 1
        # Dominant should be in the red range (quantization bins to center)
        dominant = colors[0]
        assert dominant[0] > 150
        assert dominant[1] < 100
        assert dominant[2] < 100

    def test_rgba_image_converts(self):
        """RGBA images are converted to RGB before extraction."""
        from PIL import Image

        from immich_memories.titles.colors import extract_colors_from_image

        img = Image.new("RGBA", (50, 50), (0, 255, 0, 128))
        colors = extract_colors_from_image(img, num_colors=3)
        assert len(colors) >= 1

    def test_quality_subsampling(self):
        """Higher quality value means fewer pixels sampled."""
        from PIL import Image

        from immich_memories.titles.colors import extract_colors_from_image

        img = Image.new("RGB", (100, 100), (100, 100, 100))
        colors_q1 = extract_colors_from_image(img, quality=1)
        colors_q10 = extract_colors_from_image(img, quality=10)
        # Both should return results
        assert len(colors_q1) >= 1
        assert len(colors_q10) >= 1


class TestCreateColorFadeFrames:
    """Test color fade frame generation."""

    def test_single_frame(self):
        from immich_memories.titles.colors import create_color_fade_frames

        frames = create_color_fade_frames((0, 0, 0), (255, 255, 255), 1, 10, 10)
        assert len(frames) == 1
        assert frames[0].size == (10, 10)

    def test_fade_interpolates(self):
        from immich_memories.titles.colors import create_color_fade_frames

        frames = create_color_fade_frames((0, 0, 0), (255, 255, 255), 3, 5, 5)
        assert len(frames) == 3
        # First should be black, last should be white
        first_pixel = frames[0].getpixel((0, 0))
        last_pixel = frames[2].getpixel((0, 0))
        assert first_pixel == (0, 0, 0)
        assert last_pixel == (255, 255, 255)

    def test_mid_frame_is_intermediate(self):
        from immich_memories.titles.colors import create_color_fade_frames

        frames = create_color_fade_frames((0, 0, 0), (200, 200, 200), 5, 5, 5)
        mid_pixel = frames[2].getpixel((0, 0))
        assert 80 < mid_pixel[0] < 120


# ---------------------------------------------------------------------------
# Module 1: Titles — backgrounds.py
# ---------------------------------------------------------------------------


class TestBackgroundsInterpolation:
    """Test color interpolation used in gradients."""

    def test_interpolate_midpoint(self):
        from immich_memories.titles.backgrounds import interpolate_color

        result = interpolate_color((0, 0, 0), (100, 200, 100), 0.5)
        assert result == (50, 100, 50)

    def test_interpolate_endpoints(self):
        from immich_memories.titles.backgrounds import interpolate_color

        c1, c2 = (10, 20, 30), (200, 100, 50)
        assert interpolate_color(c1, c2, 0.0) == c1
        assert interpolate_color(c1, c2, 1.0) == c2


class TestBackgroundCreation:
    """Test various background creation functions."""

    def test_radial_gradient_center_vs_edge(self):
        from immich_memories.titles.backgrounds import create_radial_gradient

        img = create_radial_gradient(200, 200, "#FFFFFF", "#000000")
        arr = np.array(img)
        center_val = arr[100, 100].mean()
        edge_val = arr[0, 0].mean()
        assert center_val > edge_val

    def test_vignette_background_center_brighter(self):
        from immich_memories.titles.backgrounds import create_vignette_background

        img = create_vignette_background(200, 200, "#808080", "#000000", strength=0.8)
        arr = np.array(img)
        center_val = arr[100, 100].mean()
        edge_val = arr[0, 0].mean()
        assert center_val > edge_val

    def test_soft_gradient_produces_image(self):
        from immich_memories.titles.backgrounds import create_soft_gradient

        img = create_soft_gradient(100, 100, ["#1A1A2E", "#16213E"])
        assert img.size == (100, 100)

    def test_soft_gradient_zero_blur(self):
        from immich_memories.titles.backgrounds import create_soft_gradient

        img = create_soft_gradient(100, 100, ["#FF0000", "#0000FF"], blur_radius=0)
        assert img.size == (100, 100)

    def test_gradient_3_colors(self):
        """Multi-stop gradient with 3 colors."""
        from immich_memories.titles.backgrounds import create_gradient_background

        img = create_gradient_background(100, 100, ["#FF0000", "#00FF00", "#0000FF"])
        assert img.size == (100, 100)

    def test_gradient_requires_2_colors(self):
        from immich_memories.titles.backgrounds import create_gradient_background

        with pytest.raises(ValueError, match="At least 2 colors"):
            create_gradient_background(100, 100, ["#FF0000"])


class TestIsDarkPalette:
    """Test dark palette detection."""

    def test_dark_colors(self):
        from immich_memories.titles.backgrounds import _is_dark_palette

        assert _is_dark_palette(["#000000", "#1A1A2E"])

    def test_light_colors(self):
        from immich_memories.titles.backgrounds import _is_dark_palette

        assert not _is_dark_palette(["#FFFFFF", "#F0F0F0"])

    def test_empty_list(self):
        from immich_memories.titles.backgrounds import _is_dark_palette

        # Empty list: total=0, avg=0, which is < 0.5
        assert _is_dark_palette([])


class TestCreateBackgroundForStyle:
    """Test the style-based background factory."""

    def test_solid_single_color(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "solid", ["#FF0000"])
        pixel = np.array(img)[25, 25]
        assert pixel[0] == 255  # Red

    def test_solid_gradient_type(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "solid_gradient", ["#FF0000", "#0000FF"])
        assert img.size == (50, 50)

    def test_soft_gradient_type(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "soft_gradient", ["#FF0000", "#0000FF"])
        assert img.size == (50, 50)

    def test_radial_gradient_type(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "radial_gradient", ["#FF0000", "#0000FF"])
        assert img.size == (50, 50)

    def test_vignette_dark_palette(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "vignette", ["#1A1A2E", "#16213E"])
        assert img.size == (50, 50)

    def test_vignette_light_palette(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "vignette", ["#FFFFFF", "#F0F0F0"])
        assert img.size == (50, 50)

    def test_unknown_type_falls_back_to_soft_gradient(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "unknown_type", ["#FF0000", "#0000FF"])
        assert img.size == (50, 50)

    def test_empty_color_list_uses_white(self):
        from immich_memories.titles.backgrounds import create_background_for_style

        img = create_background_for_style(50, 50, "solid", [])
        pixel = np.array(img)[25, 25]
        assert tuple(pixel) == (255, 255, 255)


class TestCreateBackgroundArray:
    """Test numpy array background creation."""

    def test_returns_numpy_array(self):
        from immich_memories.titles.backgrounds import create_background_array

        arr = create_background_array(50, 50, "solid_gradient", ["#FF0000", "#0000FF"])
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (50, 50, 3)


class TestCoordGridCaching:
    """Test that coordinate grids are cached for performance."""

    def test_same_dimensions_reuse_cache(self):
        from immich_memories.titles.backgrounds import _COORD_CACHE, _get_coord_grids

        y1, x1 = _get_coord_grids(77, 43)
        y2, x2 = _get_coord_grids(77, 43)
        assert y1 is y2
        assert x1 is x2
        assert (77, 43) in _COORD_CACHE


# ---------------------------------------------------------------------------
# Module 1: Titles — fonts.py
# ---------------------------------------------------------------------------


class TestFontDiscovery:
    """Test font path resolution and caching."""

    def test_bundled_font_found(self):
        """Bundled fonts should be found without network."""
        from immich_memories.titles.fonts import BUNDLED_FONTS_DIR, get_font_path

        # Skip if bundled fonts not present (dev/CI without them)
        if not BUNDLED_FONTS_DIR.exists():
            pytest.skip("Bundled fonts not present")

        path = get_font_path("Outfit", "Regular")
        if path is not None:
            assert path.exists()
            assert path.suffix == ".ttf"

    def test_unknown_font_returns_none(self):
        """Unknown font families can't be downloaded or found."""
        from immich_memories.titles.fonts import download_font

        with tempfile.TemporaryDirectory() as tmpdir:
            result = download_font("NonExistentFont123", Path(tmpdir))
            assert result is False

    def test_is_font_cached_empty_dir(self):
        from immich_memories.titles.fonts import is_font_cached

        with tempfile.TemporaryDirectory() as tmpdir:
            assert not is_font_cached("Outfit", Path(tmpdir))

    def test_is_font_cached_with_ttf(self):
        from immich_memories.titles.fonts import is_font_cached

        with tempfile.TemporaryDirectory() as tmpdir:
            font_dir = Path(tmpdir) / "Outfit"
            font_dir.mkdir()
            (font_dir / "Outfit-Regular.ttf").write_bytes(b"fake font")
            assert is_font_cached("Outfit", Path(tmpdir))

    def test_get_font_path_checks_user_cache(self):
        """User cache is checked when bundled font is missing for unknown families."""
        from immich_memories.titles.fonts import get_font_path

        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a name not in FONT_DEFINITIONS so bundled fonts won't match
            font_dir = Path(tmpdir) / "CustomFont"
            font_dir.mkdir()
            fake_font = font_dir / "CustomFont-Regular.ttf"
            fake_font.write_bytes(b"fake font data")

            path = get_font_path("CustomFont", "Regular", Path(tmpdir))
            assert path == fake_font

    def test_get_available_fonts_empty(self):
        from immich_memories.titles.fonts import get_available_fonts

        with tempfile.TemporaryDirectory() as tmpdir:
            fonts = get_available_fonts(Path(tmpdir))
            assert fonts == []

    def test_get_available_fonts_with_cached(self):
        from immich_memories.titles.fonts import get_available_fonts

        with tempfile.TemporaryDirectory() as tmpdir:
            font_dir = Path(tmpdir) / "Outfit"
            font_dir.mkdir()
            (font_dir / "Outfit-Regular.ttf").write_bytes(b"fake")
            fonts = get_available_fonts(Path(tmpdir))
            assert "Outfit" in fonts

    def test_clear_font_cache(self):
        from immich_memories.titles.fonts import clear_font_cache

        with tempfile.TemporaryDirectory() as tmpdir:
            font_dir = Path(tmpdir) / "Outfit"
            font_dir.mkdir()
            (font_dir / "Outfit-Regular.ttf").write_bytes(b"fake")
            clear_font_cache(Path(tmpdir))
            assert not font_dir.exists()


class TestFontManager:
    """Test the FontManager high-level API."""

    def test_list_cached_empty(self):
        from immich_memories.titles.fonts import FontManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = FontManager(Path(tmpdir))
            assert mgr.list_cached() == []

    def test_get_font_delegates(self):
        """get_font returns None for unknown font families when download fails."""
        from immich_memories.titles.fonts import FontManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = FontManager(Path(tmpdir))
            # Use a name not in FONT_DEFINITIONS so bundled fonts won't match
            with patch("immich_memories.titles.fonts.ensure_font_available", return_value=False):
                result = mgr.get_font("UnknownFont", "Regular")
                assert result is None

    def test_clear_cache_delegates(self):
        from immich_memories.titles.fonts import FontManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = FontManager(Path(tmpdir))
            font_dir = Path(tmpdir) / "Outfit"
            font_dir.mkdir()
            (font_dir / "test.ttf").write_bytes(b"data")
            mgr.clear_cache()
            assert not font_dir.exists()

    def test_ensure_fonts_all(self):
        """ensure_fonts(None) attempts all known fonts."""
        from immich_memories.titles.fonts import FONT_DEFINITIONS, FontManager

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "immich_memories.titles.fonts.ensure_font_available", return_value=True
            ) as mock_ensure,
        ):
            mgr = FontManager(Path(tmpdir))
            result = mgr.ensure_fonts()
            assert result is True
            assert mock_ensure.call_count == len(FONT_DEFINITIONS)

    def test_ensure_fonts_subset(self):
        from immich_memories.titles.fonts import FontManager

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("immich_memories.titles.fonts.ensure_font_available", return_value=True),
        ):
            mgr = FontManager(Path(tmpdir))
            result = mgr.ensure_fonts(["Outfit"])
            assert result is True

    def test_ensure_fonts_partial_failure(self):
        from immich_memories.titles.fonts import FontManager

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("immich_memories.titles.fonts.ensure_font_available", side_effect=[True, False]),
        ):
            mgr = FontManager(Path(tmpdir))
            result = mgr.ensure_fonts(["Outfit", "Raleway"])
            assert result is False


class TestDownloadAllFonts:
    """Test download_all_fonts behavior."""

    def test_skips_cached(self):
        from immich_memories.titles.fonts import download_all_fonts

        with tempfile.TemporaryDirectory() as tmpdir:
            font_dir = Path(tmpdir) / "Outfit"
            font_dir.mkdir()
            (font_dir / "Outfit-Regular.ttf").write_bytes(b"fake")

            # WHY: mock download_font to avoid real network calls
            with patch("immich_memories.titles.fonts.download_font") as mock_dl:
                results = download_all_fonts(Path(tmpdir))
                assert results["Outfit"] is True
                # Outfit shouldn't trigger download since it's cached
                for call in mock_dl.call_args_list:
                    assert call[0][0] != "Outfit"


# ---------------------------------------------------------------------------
# Module 1: Titles — generator.py
# ---------------------------------------------------------------------------


class TestTitleScreenConfigOutputResolution:
    """Test output resolution with exact pixel overrides."""

    def test_exact_pixel_overrides(self):
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig(resolution_width=3840, resolution_height=2160)
        assert config.output_resolution == (3840, 2160)

    def test_partial_override_uses_tier(self):
        """When only width is set but not height, tier lookup is used."""
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig(resolution_width=3840)
        assert config.output_resolution == (1920, 1080)  # default tier


class TestTitleScreenGeneratorStyleInit:
    """Test the style initialization logic in TitleScreenGenerator."""

    def test_named_style_mode(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
        from immich_memories.titles.styles import PRESET_STYLES

        preset_name = list(PRESET_STYLES.keys())[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(style_mode=preset_name)
            # WHY: mock RenderingService to avoid GPU/taichi init
            with patch("immich_memories.titles.generator.RenderingService"):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                assert gen.style.name == preset_name

    def test_auto_mode_no_mood_falls_back_to_random(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(style_mode="auto")
            with patch("immich_memories.titles.generator.RenderingService"):
                gen = TitleScreenGenerator(config=config, mood=None, output_dir=Path(tmpdir))
                assert gen.style is not None

    def test_explicit_style_passed_through(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
        from immich_memories.titles.styles import TitleStyle

        custom_style = TitleStyle(name="custom_test")
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig()
            with patch("immich_memories.titles.generator.RenderingService"):
                gen = TitleScreenGenerator(
                    config=config, style=custom_style, output_dir=Path(tmpdir)
                )
                assert gen.style.name == "custom_test"

    def test_decorative_lines_disabled(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(name="test", use_line_accent=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(show_decorative_lines=False)
            with patch("immich_memories.titles.generator.RenderingService"):
                gen = TitleScreenGenerator(config=config, style=style, output_dir=Path(tmpdir))
                assert not gen.style.use_line_accent


class TestGenerateTitleScreenWithOverride:
    """Test title generation with LLM-generated title override."""

    def test_title_override_bypasses_template(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(
                title_override="Custom Title",
                subtitle_override="Custom Sub",
            )
            # WHY: mock RenderingService to avoid video encoding
            mock_rendering = MagicMock()
            with patch(
                "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                result = gen.generate_title_screen(year=2024)

                assert result.screen_type == "title"
                # Verify the rendering was called with the override title
                call_kwargs = mock_rendering.create_title_video.call_args
                assert call_kwargs.kwargs["title"] == "Custom Title"
                assert call_kwargs.kwargs["subtitle"] == "Custom Sub"


class TestGenerateMonthDivider:
    """Test month divider generation with birthday flag."""

    def test_birthday_month_uses_bounce_animation(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig()
            mock_rendering = MagicMock()
            with patch(
                "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                result = gen.generate_month_divider(6, year=2024, is_birthday_month=True)

                assert result.screen_type == "month_divider"
                call_kwargs = mock_rendering.create_title_video.call_args
                style_arg = call_kwargs.kwargs["style"]
                assert style_arg.animation_preset == "bounce_in"
                assert call_kwargs.kwargs["is_birthday"] is True


class TestGenerateYearDivider:
    """Test year divider screen generation."""

    def test_year_divider_output(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig()
            mock_rendering = MagicMock()
            with patch(
                "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                result = gen.generate_year_divider(2025)

                assert result.screen_type == "year_divider"
                call_kwargs = mock_rendering.create_title_video.call_args
                assert call_kwargs.kwargs["title"] == "2025"


class TestGenerateEndingScreen:
    """Test ending screen generation branches."""

    def test_ending_without_content_clip(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig()
            mock_rendering = MagicMock()
            mock_ending = MagicMock()
            with (
                patch(
                    "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
                ),
                patch("immich_memories.titles.generator.EndingService", return_value=mock_ending),
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                result = gen.generate_ending_screen()

                assert result.screen_type == "ending"
                mock_ending.create_ending_video.assert_called_once()

    def test_ending_with_content_clip_uses_rendering_service(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator
        from immich_memories.titles.styles import TitleStyle

        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"fake")
            config = TitleScreenConfig()
            style = TitleStyle(name="test", background_type="content_backed")
            mock_rendering = MagicMock()
            with patch(
                "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
            ):
                gen = TitleScreenGenerator(config=config, style=style, output_dir=Path(tmpdir))
                result = gen.generate_ending_screen(content_clip_path=clip_path)

                assert result.screen_type == "ending"
                mock_rendering.create_title_video.assert_called_once()
                call_kwargs = mock_rendering.create_title_video.call_args
                assert call_kwargs.kwargs["is_ending"] is True
                assert call_kwargs.kwargs["fade_to_white"] is True


class TestGenerateAllScreens:
    """Test the generate_all_screens orchestrator."""

    def test_generates_title_and_ending(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(show_month_dividers=False)
            mock_rendering = MagicMock()
            mock_ending = MagicMock()
            with (
                patch(
                    "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
                ),
                patch("immich_memories.titles.generator.EndingService", return_value=mock_ending),
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                screens = gen.generate_all_screens(year=2024)

                assert "title" in screens
                assert "ending" in screens

    def test_generates_month_dividers_when_multiple(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(show_month_dividers=True)
            mock_rendering = MagicMock()
            mock_ending = MagicMock()
            with (
                patch(
                    "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
                ),
                patch("immich_memories.titles.generator.EndingService", return_value=mock_ending),
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                screens = gen.generate_all_screens(year=2024, months_in_video=[1, 3, 6])

                assert "month_01" in screens
                assert "month_03" in screens
                assert "month_06" in screens

    def test_skips_dividers_for_single_month(self):
        from immich_memories.titles.generator import TitleScreenConfig, TitleScreenGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(show_month_dividers=True)
            mock_rendering = MagicMock()
            mock_ending = MagicMock()
            with (
                patch(
                    "immich_memories.titles.generator.RenderingService", return_value=mock_rendering
                ),
                patch("immich_memories.titles.generator.EndingService", return_value=mock_ending),
            ):
                gen = TitleScreenGenerator(config=config, output_dir=Path(tmpdir))
                screens = gen.generate_all_screens(year=2024, months_in_video=[6])

                assert "month_06" not in screens


# ---------------------------------------------------------------------------
# Module 1: Titles — rendering_service.py
# ---------------------------------------------------------------------------


class TestRenderingServiceInit:
    """Test renderer selection logic."""

    def test_gpu_disabled_by_config(self):
        from immich_memories.titles.generator import TitleScreenConfig
        from immich_memories.titles.rendering_service import RenderingService

        config = TitleScreenConfig(use_gpu_rendering=False)
        svc = RenderingService(config)
        assert not svc.use_gpu

    def test_gpu_enabled_but_taichi_unavailable(self):
        from immich_memories.titles.generator import TitleScreenConfig
        from immich_memories.titles.rendering_service import RenderingService

        config = TitleScreenConfig(use_gpu_rendering=True)
        with patch("immich_memories.titles.rendering_service.TAICHI_AVAILABLE", False):
            svc = RenderingService(config)
            assert not svc.use_gpu

    def test_pil_fallback_extracts_blurred_frame(self):
        """When GPU is off and content_clip_path given, try to extract blurred frame."""
        from immich_memories.titles.generator import TitleScreenConfig
        from immich_memories.titles.rendering_service import RenderingService

        config = TitleScreenConfig(use_gpu_rendering=False)
        svc = RenderingService(config)

        fake_clip = Path("/tmp/fake_clip.mp4")
        # WHY: mock create_title_video to avoid actual video encoding
        with (
            patch("immich_memories.titles.rendering_service.create_title_video") as mock_create,
            # WHY: mock _extract_blurred_frame to simulate ffmpeg I/O
            patch.object(
                svc, "_extract_blurred_frame", return_value=np.zeros((100, 100, 3))
            ) as mock_extract,
        ):
            from immich_memories.titles.styles import TitleStyle

            svc.create_title_video(
                title="Test",
                subtitle=None,
                style=TitleStyle(name="test"),
                output_path=Path("/tmp/out.mp4"),
                width=100,
                height=100,
                duration=1.0,
                fps=30.0,
                animated_background=True,
                content_clip_path=fake_clip,
            )
            mock_extract.assert_called_once_with(fake_clip, 100, 100)
            mock_create.assert_called_once()
            # background_image should be the extracted frame
            assert mock_create.call_args.kwargs["background_image"] is not None


class TestExtractBlurredFrame:
    """Test the static blurred frame extraction method."""

    def test_extraction_failure_returns_none(self):
        from immich_memories.titles.rendering_service import RenderingService

        # WHY: mock subprocess.run to simulate ffmpeg failure (imported inside method)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = RenderingService._extract_blurred_frame(Path("/fake.mp4"), 100, 100)
            assert result is None

    def test_extraction_exception_returns_none(self):
        from immich_memories.titles.rendering_service import RenderingService

        # WHY: mock subprocess.run to simulate missing ffmpeg
        with patch("subprocess.run", side_effect=OSError("ffmpeg not found")):
            result = RenderingService._extract_blurred_frame(Path("/fake.mp4"), 100, 100)
            assert result is None


# ---------------------------------------------------------------------------
# Module 1: Titles — convenience.py
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """Test convenience wrapper functions."""

    def test_generate_title_screen_defaults(self):
        """Convenience function uses random style and default path."""
        from immich_memories.titles.convenience import generate_title_screen

        # WHY: mock create_title_video to avoid actual video encoding
        with patch(
            "immich_memories.titles.convenience.create_title_video",
            return_value=Path("/tmp/out.mp4"),
        ):
            result = generate_title_screen("2024", subtitle="Memories")
            assert result == Path("/tmp/out.mp4")

    def test_generate_month_divider_defaults(self):
        from immich_memories.titles.convenience import generate_month_divider

        with patch(
            "immich_memories.titles.convenience.create_title_video",
            return_value=Path("/tmp/out.mp4"),
        ):
            result = generate_month_divider(6, year=2024)
            assert result == Path("/tmp/out.mp4")

    def test_generate_ending_screen_delegates(self):
        from immich_memories.titles.convenience import generate_ending_screen

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "ending.mp4"
            mock_screen = MagicMock()
            mock_screen.path = output
            # WHY: mock TitleScreenGenerator to avoid GPU init and video encoding
            # The import is inside the function body, so patch at source module
            with patch("immich_memories.titles.generator.TitleScreenGenerator") as mock_gen_cls:
                mock_gen_cls.return_value.generate_ending_screen.return_value = mock_screen
                result = generate_ending_screen(output_path=output)
                assert result == output

    def test_generate_ending_screen_renames_if_path_differs(self):
        from immich_memories.titles.convenience import generate_ending_screen

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "my_ending.mp4"
            # Generator writes to a different path
            gen_path = Path(tmpdir) / "ending_screen.mp4"
            gen_path.write_bytes(b"fake video")
            mock_screen = MagicMock()
            mock_screen.path = gen_path
            with patch("immich_memories.titles.generator.TitleScreenGenerator") as mock_gen_cls:
                mock_gen_cls.return_value.generate_ending_screen.return_value = mock_screen
                result = generate_ending_screen(output_path=output)
                assert result == output


# ---------------------------------------------------------------------------
# Module 2: Audio — mixer_helpers.py
# ---------------------------------------------------------------------------


class TestStemDuckingLevels:
    """Test StemDuckingLevels defaults and customization."""

    def test_default_ducking_levels(self):
        from immich_memories.audio.mixer_helpers import StemDuckingLevels

        levels = StemDuckingLevels()
        assert levels.drums_db == -3.0
        assert levels.bass_db == -6.0
        assert levels.vocals_db == -12.0
        assert levels.other_db == -9.0

    def test_custom_ducking_levels(self):
        from immich_memories.audio.mixer_helpers import StemDuckingLevels

        levels = StemDuckingLevels(drums_db=-1.0, bass_db=-2.0, vocals_db=-6.0, other_db=-4.0)
        assert levels.drums_db == -1.0
        assert levels.bass_db == -2.0


class TestMixAudioWithStemDucking:
    """Test 2-stem ducking filter graph construction."""

    def test_builds_filter_graph_and_calls_ffmpeg(self):
        from immich_memories.audio.mixer_helpers import mix_audio_with_stem_ducking

        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "video.mp4"
            vocals = Path(tmpdir) / "vocals.wav"
            accomp = Path(tmpdir) / "accomp.wav"
            output = Path(tmpdir) / "output.mp4"

            # WHY: mock get_video_duration — real probe needs actual media
            with (
                patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=60.0),
                # WHY: mock subprocess to avoid actual ffmpeg execution
                patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
            ):
                mock_sub.run.return_value = MagicMock(returncode=0)
                mock_sub.CalledProcessError = type("CalledProcessError", (Exception,), {})

                result = mix_audio_with_stem_ducking(video, vocals, accomp, output)
                assert result == output
                mock_sub.run.assert_called_once()

                # Verify the command includes correct inputs
                cmd = mock_sub.run.call_args[0][0]
                assert str(video) in cmd
                assert str(vocals) in cmd
                assert str(accomp) in cmd

    def test_ffmpeg_failure_raises(self):
        import subprocess as real_subprocess

        from immich_memories.audio.mixer_helpers import mix_audio_with_stem_ducking

        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "video.mp4"

            with (
                patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=60.0),
                patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
            ):
                mock_sub.CalledProcessError = real_subprocess.CalledProcessError
                mock_sub.run.side_effect = real_subprocess.CalledProcessError(
                    1, "ffmpeg", stderr=b"error"
                )

                with pytest.raises(real_subprocess.CalledProcessError):
                    mix_audio_with_stem_ducking(
                        video, Path("v.wav"), Path("a.wav"), Path("out.mp4")
                    )

    def test_normalize_audio_uses_loudnorm(self):
        from immich_memories.audio.mixer import DuckingConfig, MixConfig
        from immich_memories.audio.mixer_helpers import mix_audio_with_stem_ducking

        with tempfile.TemporaryDirectory():
            config = MixConfig(ducking=DuckingConfig(), normalize_audio=True)

            with (
                patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=30.0),
                patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
            ):
                mock_sub.run.return_value = MagicMock(returncode=0)
                mock_sub.CalledProcessError = type("CalledProcessError", (Exception,), {})

                mix_audio_with_stem_ducking(
                    Path("v.mp4"),
                    Path("voc.wav"),
                    Path("acc.wav"),
                    Path("out.mp4"),
                    config=config,
                )

                cmd = mock_sub.run.call_args[0][0]
                filter_arg = cmd[cmd.index("-filter_complex") + 1]
                assert "loudnorm" in filter_arg

    def test_no_normalize_uses_acopy(self):
        from immich_memories.audio.mixer import DuckingConfig, MixConfig
        from immich_memories.audio.mixer_helpers import mix_audio_with_stem_ducking

        config = MixConfig(ducking=DuckingConfig(), normalize_audio=False)

        with (
            patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=30.0),
            patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = MagicMock(returncode=0)
            mock_sub.CalledProcessError = type("CalledProcessError", (Exception,), {})

            mix_audio_with_stem_ducking(
                Path("v.mp4"),
                Path("voc.wav"),
                Path("acc.wav"),
                Path("out.mp4"),
                config=config,
            )

            cmd = mock_sub.run.call_args[0][0]
            filter_arg = cmd[cmd.index("-filter_complex") + 1]
            assert "acopy" in filter_arg


class TestMixAudioWith4StemDucking:
    """Test 4-stem ducking builds correct filter graph."""

    def test_builds_5_input_mix(self):
        from immich_memories.audio.mixer_helpers import mix_audio_with_4stem_ducking

        with (
            patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=60.0),
            patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = MagicMock(returncode=0)
            mock_sub.CalledProcessError = type("CalledProcessError", (Exception,), {})

            result = mix_audio_with_4stem_ducking(
                Path("v.mp4"),
                Path("drums.wav"),
                Path("bass.wav"),
                Path("vocals.wav"),
                Path("other.wav"),
                Path("out.mp4"),
            )
            assert result == Path("out.mp4")

            cmd = mock_sub.run.call_args[0][0]
            filter_arg = cmd[cmd.index("-filter_complex") + 1]
            # Should have 5-input amix
            assert "amix=inputs=5" in filter_arg
            # Drums should not be sidechain-compressed
            assert "final_drums" in filter_arg
            assert "ducked_bass" in filter_arg
            assert "ducked_vocals" in filter_arg

    def test_fade_in_and_out_applied_to_stems(self):
        from immich_memories.audio.mixer import DuckingConfig, MixConfig
        from immich_memories.audio.mixer_helpers import mix_audio_with_4stem_ducking

        config = MixConfig(
            ducking=DuckingConfig(),
            fade_in_seconds=1.5,
            fade_out_seconds=2.5,
            music_starts_at=0.5,
        )

        with (
            patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=60.0),
            patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = MagicMock(returncode=0)
            mock_sub.CalledProcessError = type("CalledProcessError", (Exception,), {})

            mix_audio_with_4stem_ducking(
                Path("v.mp4"),
                Path("d.wav"),
                Path("b.wav"),
                Path("v.wav"),
                Path("o.wav"),
                Path("out.mp4"),
                config=config,
            )

            cmd = mock_sub.run.call_args[0][0]
            filter_arg = cmd[cmd.index("-filter_complex") + 1]
            assert "afade=t=in" in filter_arg
            assert "afade=t=out" in filter_arg

    def test_4stem_ffmpeg_failure_raises(self):
        import subprocess as real_subprocess

        from immich_memories.audio.mixer_helpers import mix_audio_with_4stem_ducking

        with (
            patch("immich_memories.audio.mixer_helpers.get_video_duration", return_value=60.0),
            patch("immich_memories.audio.mixer_helpers.subprocess") as mock_sub,
        ):
            mock_sub.CalledProcessError = real_subprocess.CalledProcessError
            mock_sub.run.side_effect = real_subprocess.CalledProcessError(
                1, "ffmpeg", stderr=b"error"
            )

            with pytest.raises(real_subprocess.CalledProcessError):
                mix_audio_with_4stem_ducking(
                    Path("v.mp4"),
                    Path("d.wav"),
                    Path("b.wav"),
                    Path("v.wav"),
                    Path("o.wav"),
                    Path("out.mp4"),
                )


# ---------------------------------------------------------------------------
# Module 2: Audio — mood_analyzer_backends.py
# ---------------------------------------------------------------------------


class TestOllamaAnalyzerClient:
    """Test Ollama analyzer client lifecycle."""

    def test_client_lazy_init(self):
        from immich_memories.audio.mood_analyzer_backends import OllamaMoodAnalyzer

        analyzer = OllamaMoodAnalyzer()
        assert analyzer._client is None
        client = analyzer.client
        assert client is not None

    @pytest.mark.asyncio
    async def test_close_client(self):
        from immich_memories.audio.mood_analyzer_backends import OllamaMoodAnalyzer

        analyzer = OllamaMoodAnalyzer()
        _ = analyzer.client  # Force creation
        await analyzer.close()
        assert analyzer._client is None

    @pytest.mark.asyncio
    async def test_is_available_returns_false_on_error(self):
        from immich_memories.audio.mood_analyzer_backends import OllamaMoodAnalyzer

        analyzer = OllamaMoodAnalyzer(base_url="http://localhost:99999")
        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        analyzer._client = mock_client

        # httpx.HTTPError is a subclass — should return False
        import httpx

        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        available = await analyzer.is_available()
        assert not available

    @pytest.mark.asyncio
    async def test_analyze_video_no_frames_returns_default(self):
        from immich_memories.audio.mood_analyzer_backends import OllamaMoodAnalyzer

        analyzer = OllamaMoodAnalyzer()
        # WHY: mock extract_keyframes to avoid ffmpeg dependency
        with patch.object(analyzer, "extract_keyframes", return_value=[]):
            mood = await analyzer.analyze_video(Path("/fake/video.mp4"))
            assert mood.primary_mood == "calm"
            assert mood.confidence == 0.3


class TestOpenAIAnalyzerClient:
    """Test OpenAI-compatible analyzer client lifecycle."""

    def test_client_with_api_key(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(api_key="test-key-123")
        client = analyzer.client
        assert "Authorization" in client.headers

    def test_client_without_api_key(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(api_key="")
        client = analyzer.client
        assert "Authorization" not in client.headers

    @pytest.mark.asyncio
    async def test_close_client(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(api_key="key")
        _ = analyzer.client
        await analyzer.close()
        assert analyzer._client is None

    @pytest.mark.asyncio
    async def test_analyze_video_no_frames_returns_default(self):
        from immich_memories.audio.mood_analyzer_backends import OpenAICompatibleMoodAnalyzer

        analyzer = OpenAICompatibleMoodAnalyzer(api_key="key")
        with patch.object(analyzer, "extract_keyframes", return_value=[]):
            mood = await analyzer.analyze_video(Path("/fake/video.mp4"))
            assert mood.primary_mood == "calm"
            assert mood.confidence == 0.3


class TestGetMoodAnalyzer:
    """Test the mood analyzer factory function."""

    @pytest.mark.asyncio
    async def test_openai_compatible_provider(self):
        from immich_memories.audio.mood_analyzer_backends import (
            OpenAICompatibleMoodAnalyzer,
            get_mood_analyzer,
        )

        analyzer = await get_mood_analyzer(
            provider="openai-compatible",
            base_url="http://localhost:8080/v1",
            model="test-model",
            api_key="key",
        )
        assert isinstance(analyzer, OpenAICompatibleMoodAnalyzer)
        assert analyzer.model == "test-model"

    @pytest.mark.asyncio
    async def test_ollama_provider_unavailable(self):
        from immich_memories.audio.mood_analyzer_backends import get_mood_analyzer

        # WHY: mock is_available to avoid actual network call
        with (
            patch(
                "immich_memories.audio.mood_analyzer_backends.OllamaMoodAnalyzer.is_available",
                new_callable=AsyncMock,
                return_value=False,
            ),
            pytest.raises(RuntimeError, match="Ollama not available"),
        ):
            await get_mood_analyzer(provider="ollama", model="llava")

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self):
        from immich_memories.audio.mood_analyzer_backends import get_mood_analyzer

        with pytest.raises(RuntimeError, match="Unknown LLM provider"):
            await get_mood_analyzer(provider="deepseek")


class TestGetMoodAnalyzerFromConfig:
    """Test config-based mood analyzer factory."""

    @pytest.mark.asyncio
    async def test_delegates_to_get_mood_analyzer(self):
        from immich_memories.audio.mood_analyzer_backends import get_mood_analyzer_from_config
        from immich_memories.config_models import LLMConfig

        config = LLMConfig(
            provider="openai-compatible",
            base_url="http://test:8080/v1",
            model="test",
            api_key="key",
        )
        analyzer = await get_mood_analyzer_from_config(config)
        assert analyzer.model == "test"


# ---------------------------------------------------------------------------
# Module 3: Misc — filename_builder.py
# ---------------------------------------------------------------------------


class TestBuildOutputFilename:
    """Test filename generation for various memory types."""

    def test_year_in_review(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "year_in_review",
            {"year": 2025},
            None,
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        assert result == "everyone_2025_memories.mp4"

    def test_single_person_year(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "year_in_review",
            {"person_names": ["Alice"], "year": 2025},
            None,
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        assert result == "alice_2025_memories.mp4"

    def test_multi_person(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "multi_person",
            {"person_names": ["Sam", "Emile"]},
            None,
            date(2025, 3, 1),
            date(2025, 3, 31),
        )
        assert "sam_emile" in result

    def test_multi_person_many_names_truncated(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "multi_person",
            {"person_names": ["A", "B", "C", "D"]},
            None,
            None,
            None,
        )
        assert "and_others" in result

    def test_trip_with_location(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "trip",
            {"location_name": "Paris, France"},
            None,
            None,
            None,
        )
        assert "trip" in result
        assert "paris" in result

    def test_trip_without_location_uses_dates(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "trip",
            {},
            None,
            date(2025, 6, 1),
            date(2025, 6, 15),
        )
        assert "trip" in result
        assert "june_2025" in result

    def test_season_preset(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "season",
            {"season": "summer", "year": 2025},
            None,
            None,
            None,
        )
        assert "summer_2025" in result

    def test_monthly_highlights(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "monthly_highlights",
            {"month": 3, "year": 2026},
            None,
            None,
            None,
        )
        assert "march_2026" in result

    def test_monthly_highlights_no_year(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "monthly_highlights",
            {"month": 3},
            None,
            None,
            None,
        )
        assert "march" in result

    def test_on_this_day(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "on_this_day",
            {},
            None,
            date(2025, 3, 12),
            None,
        )
        assert "march_12" in result

    def test_no_context_at_all(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(None, {}, None, None, None)
        assert result == "everyone_memories.mp4"

    def test_person_from_state_fallback(self):
        """Falls back to person_name when preset_params has no names."""
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "year_in_review",
            {"year": 2025},
            "Bob",
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        assert "bob" in result

    def test_trip_fallback_to_year(self):
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "trip",
            {"year": 2025},
            None,
            None,
            None,
        )
        assert "2025" in result

    def test_fallback_to_preset_year(self):
        """When no dates but year in preset, uses the year."""
        from immich_memories.filename_builder import build_output_filename

        result = build_output_filename(
            "custom",
            {"year": 2025},
            None,
            None,
            None,
        )
        assert "2025" in result


class TestDateRangeSlug:
    """Test the _date_range_slug helper."""

    def test_full_calendar_year(self):
        from immich_memories.filename_builder import _date_range_slug

        assert _date_range_slug(date(2025, 1, 1), date(2025, 12, 31)) == "2025"

    def test_same_month(self):
        from immich_memories.filename_builder import _date_range_slug

        assert _date_range_slug(date(2025, 6, 5), date(2025, 6, 28)) == "june_2025"

    def test_same_year_different_months(self):
        from immich_memories.filename_builder import _date_range_slug

        result = _date_range_slug(date(2025, 1, 1), date(2025, 4, 30))
        assert result == "jan-apr_2025"

    def test_cross_year(self):
        from immich_memories.filename_builder import _date_range_slug

        result = _date_range_slug(date(2024, 11, 1), date(2025, 2, 28))
        assert result == "20241101-20250228"


class TestBuildTitlePersonName:
    """Test person name formatting for title screens."""

    def test_multi_person_two_names(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name(
            "multi_person", {"person_names": ["Alice Smith", "Bob Jones"]}, None
        )
        assert result == "Alice & Bob"

    def test_multi_person_three_names(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name(
            "multi_person", {"person_names": ["Alice Smith", "Bob Jones", "Charlie Brown"]}, None
        )
        assert result == "Alice, Bob & Charlie"

    def test_single_person_from_preset(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name("year_in_review", {"person_names": ["Alice Smith"]}, None)
        assert result == "Alice"

    def test_single_person_full_name(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name(
            "year_in_review",
            {"person_names": ["Alice Smith"]},
            None,
            use_first_name_only=False,
        )
        assert result == "Alice Smith"

    def test_person_from_state(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name(None, {}, "Bob Jones")
        assert result == "Bob"

    def test_no_person(self):
        from immich_memories.filename_builder import build_title_person_name

        result = build_title_person_name(None, {}, None)
        assert result is None


class TestShouldShowMonthDividers:
    """Test month divider visibility logic."""

    def test_monthly_highlights_always_false(self):
        from immich_memories.filename_builder import should_show_month_dividers

        assert not should_show_month_dividers("monthly_highlights", None, None)

    def test_on_this_day_always_false(self):
        from immich_memories.filename_builder import should_show_month_dividers

        assert not should_show_month_dividers("on_this_day", None, None)

    def test_no_dates_returns_true(self):
        from immich_memories.filename_builder import should_show_month_dividers

        assert should_show_month_dividers("year_in_review", None, None)

    def test_short_range_returns_false(self):
        from immich_memories.filename_builder import should_show_month_dividers

        assert not should_show_month_dividers("custom", date(2025, 1, 1), date(2025, 3, 31))

    def test_long_range_returns_true(self):
        from immich_memories.filename_builder import should_show_month_dividers

        assert should_show_month_dividers("custom", date(2025, 1, 1), date(2025, 12, 31))


class TestGetDividerMode:
    """Test divider mode selection logic."""

    def test_monthly_highlights_returns_none(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("monthly_highlights", None, None) == "none"

    def test_trip_returns_none(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("trip", None, None) == "none"

    def test_on_this_day_returns_year(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("on_this_day", None, None) == "year"

    def test_no_dates_returns_month(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("year_in_review", None, None) == "month"

    def test_short_range_returns_none(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("custom", date(2025, 1, 1), date(2025, 2, 28)) == "none"

    def test_multi_year_returns_year(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("custom", date(2024, 1, 1), date(2025, 12, 31)) == "year"

    def test_single_year_long_range_returns_month(self):
        from immich_memories.filename_builder import get_divider_mode

        assert get_divider_mode("custom", date(2025, 1, 1), date(2025, 12, 31)) == "month"


# ---------------------------------------------------------------------------
# Module 3: Misc — search_service.py
# ---------------------------------------------------------------------------


class TestSearchServiceMetadata:
    """Test search metadata query construction."""

    @pytest.mark.asyncio
    async def test_search_metadata_basic(self):
        from immich_memories.api.search_service import SearchService

        # WHY: mock the HTTP request function — avoids real Immich server
        mock_request = AsyncMock(
            return_value={
                "assets": {
                    "total": 0,
                    "count": 0,
                    "items": [],
                    "nextPage": None,
                }
            }
        )
        svc = SearchService(mock_request)
        result = await svc.search_metadata(page=1, size=50)

        assert result.total == 0
        assert result.all_assets == []
        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["page"] == 1
        assert payload["size"] == 50

    @pytest.mark.asyncio
    async def test_search_metadata_with_filters(self):
        from immich_memories.api.models import AssetType
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(
            return_value={"assets": {"total": 0, "count": 0, "items": [], "nextPage": None}}
        )
        svc = SearchService(mock_request)
        taken_after = datetime(2024, 1, 1)
        taken_before = datetime(2024, 12, 31)

        await svc.search_metadata(
            person_ids=["p1"],
            asset_type=AssetType.VIDEO,
            taken_after=taken_after,
            taken_before=taken_before,
        )

        payload = mock_request.call_args.kwargs["json"]
        assert payload["personIds"] == ["p1"]
        assert payload["type"] == "VIDEO"
        assert "takenAfter" in payload
        assert "takenBefore" in payload


def _make_asset(asset_id: str, created: str, is_live: bool = False) -> dict:
    """Helper to create a minimal asset dict for search responses."""
    return {
        "id": asset_id,
        "type": "VIDEO",
        "fileCreatedAt": created,
        "fileModifiedAt": created,
        "updatedAt": created,
        "livePhotoVideoId": "live-123" if is_live else None,
    }


class TestSearchServicePagination:
    """Test pagination logic in search methods."""

    @pytest.mark.asyncio
    async def test_get_all_videos_for_year_paginates(self):
        from immich_memories.api.search_service import SearchService

        page1 = {
            "assets": {
                "total": 2,
                "count": 1,
                "items": [_make_asset("a1", "2024-01-15T00:00:00")],
                "nextPage": "2",
            }
        }
        page2 = {
            "assets": {
                "total": 2,
                "count": 1,
                "items": [_make_asset("a2", "2024-06-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(side_effect=[page1, page2])
        svc = SearchService(mock_request)

        collected = []

        def progress(count, total):
            collected.append(count)

        result = await svc.get_all_videos_for_year(2024, progress_callback=progress)
        assert len(result) == 2
        assert result[0].id == "a1"
        assert result[1].id == "a2"
        assert 2 in collected

    @pytest.mark.asyncio
    async def test_get_videos_for_person_and_year(self):
        from immich_memories.api.search_service import SearchService

        response = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [_make_asset("a1", "2024-03-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)
        result = await svc.get_videos_for_person_and_year("p1", 2024)

        assert len(result) == 1
        payload = mock_request.call_args.kwargs["json"]
        assert payload["personIds"] == ["p1"]


class TestSearchServiceDateRange:
    """Test date range search methods."""

    @pytest.mark.asyncio
    async def test_get_videos_for_date_range(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        response = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [_make_asset("a1", "2024-06-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30))
        result = await svc.get_videos_for_date_range(dr)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_videos_for_person_and_date_range(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        response = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [_make_asset("a1", "2024-06-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30))
        result = await svc.get_videos_for_person_and_date_range("p1", dr)

        assert len(result) == 1


class TestSearchServiceMultiPerson:
    """Test multi-person search (union and intersection)."""

    @pytest.mark.asyncio
    async def test_get_videos_for_any_person_union(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        # Person 1 has asset a1, person 2 has a1 + a2
        resp_p1 = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [_make_asset("a1", "2024-01-15T00:00:00")],
                "nextPage": None,
            }
        }
        resp_p2 = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [
                    _make_asset("a1", "2024-01-15T00:00:00"),
                    _make_asset("a2", "2024-02-15T00:00:00"),
                ],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(side_effect=[resp_p1, resp_p2])
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31))

        result = await svc.get_videos_for_any_person(["p1", "p2"], dr)
        assert len(result) == 2  # Union: a1, a2 (deduplicated)

    @pytest.mark.asyncio
    async def test_get_videos_for_any_person_empty_list(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        mock_request = AsyncMock()
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31))

        result = await svc.get_videos_for_any_person([], dr)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_videos_for_all_persons_intersection(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        # Person 1 has a1, a2; person 2 has a2, a3 — intersection = a2
        resp_p1 = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [
                    _make_asset("a1", "2024-01-15T00:00:00"),
                    _make_asset("a2", "2024-02-15T00:00:00"),
                ],
                "nextPage": None,
            }
        }
        resp_p2 = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [
                    _make_asset("a2", "2024-02-15T00:00:00"),
                    _make_asset("a3", "2024-03-15T00:00:00"),
                ],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(side_effect=[resp_p1, resp_p2])
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31))

        result = await svc.get_videos_for_all_persons(["p1", "p2"], dr)
        assert len(result) == 1
        assert result[0].id == "a2"

    @pytest.mark.asyncio
    async def test_get_videos_for_all_persons_empty_list(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        mock_request = AsyncMock()
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31))

        result = await svc.get_videos_for_all_persons([], dr)
        assert result == []


class TestSearchServiceTimeBuckets:
    """Test time bucket queries."""

    @pytest.mark.asyncio
    async def test_get_time_buckets(self):
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(
            return_value=[
                {"count": 5, "timeBucket": "2024-01-01T00:00:00.000Z"},
                {"count": 3, "timeBucket": "2024-02-01T00:00:00.000Z"},
            ]
        )
        svc = SearchService(mock_request)
        buckets = await svc.get_time_buckets(size="MONTH")
        assert len(buckets) == 2
        assert buckets[0].count == 5

    @pytest.mark.asyncio
    async def test_get_time_buckets_with_filters(self):
        from immich_memories.api.models import AssetType
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(return_value=[])
        svc = SearchService(mock_request)
        await svc.get_time_buckets(asset_type=AssetType.VIDEO, person_id="p1")

        params = mock_request.call_args.kwargs["params"]
        assert params["type"] == "VIDEO"
        assert params["personId"] == "p1"

    @pytest.mark.asyncio
    async def test_get_available_years(self):
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(
            return_value=[
                {"count": 5, "timeBucket": "2024-01-01T00:00:00.000Z"},
                {"count": 3, "timeBucket": "2023-06-01T00:00:00.000Z"},
                {"count": 1, "timeBucket": "2024-07-01T00:00:00.000Z"},
            ]
        )
        svc = SearchService(mock_request)
        years = await svc.get_available_years()
        assert years == [2024, 2023]  # Sorted descending, deduplicated

    @pytest.mark.asyncio
    async def test_get_available_years_handles_invalid_bucket(self):
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(
            return_value=[
                {"count": 5, "timeBucket": "2024-01-01T00:00:00.000Z"},
                {"count": 1, "timeBucket": "invalid-date"},
            ]
        )
        svc = SearchService(mock_request)
        years = await svc.get_available_years()
        assert years == [2024]

    @pytest.mark.asyncio
    async def test_get_bucket_assets(self):
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(
            return_value=[
                _make_asset("a1", "2024-01-15T00:00:00"),
            ]
        )
        svc = SearchService(mock_request)
        assets = await svc.get_bucket_assets("2024-01-01T00:00:00.000Z")
        assert len(assets) == 1
        assert assets[0].id == "a1"

    @pytest.mark.asyncio
    async def test_get_bucket_assets_with_filters(self):
        from immich_memories.api.models import AssetType
        from immich_memories.api.search_service import SearchService

        mock_request = AsyncMock(return_value=[])
        svc = SearchService(mock_request)
        await svc.get_bucket_assets(
            "2024-01-01T00:00:00.000Z",
            asset_type=AssetType.VIDEO,
            person_id="p1",
        )
        params = mock_request.call_args.kwargs["params"]
        assert params["type"] == "VIDEO"
        assert params["personId"] == "p1"


class TestSearchServiceLivePhotos:
    """Test live photo search methods."""

    @pytest.mark.asyncio
    async def test_get_live_photos_single_person(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        response = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [
                    {
                        "id": "lp1",
                        "type": "IMAGE",
                        "fileCreatedAt": "2024-06-15T00:00:00",
                        "fileModifiedAt": "2024-06-15T00:00:00",
                        "updatedAt": "2024-06-15T00:00:00",
                        "livePhotoVideoId": "vid-1",
                    },
                    {
                        "id": "img1",
                        "type": "IMAGE",
                        "fileCreatedAt": "2024-06-16T00:00:00",
                        "fileModifiedAt": "2024-06-16T00:00:00",
                        "updatedAt": "2024-06-16T00:00:00",
                        "livePhotoVideoId": None,
                    },
                ],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30))

        result = await svc.get_live_photos_for_date_range(dr, person_id="p1")
        assert len(result) == 1
        assert result[0].id == "lp1"

    @pytest.mark.asyncio
    async def test_get_photos_excludes_live_photos(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        response = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [
                    {
                        "id": "lp1",
                        "type": "IMAGE",
                        "fileCreatedAt": "2024-06-15T00:00:00",
                        "fileModifiedAt": "2024-06-15T00:00:00",
                        "updatedAt": "2024-06-15T00:00:00",
                        "livePhotoVideoId": "vid-1",
                    },
                    {
                        "id": "img1",
                        "type": "IMAGE",
                        "fileCreatedAt": "2024-06-16T00:00:00",
                        "fileModifiedAt": "2024-06-16T00:00:00",
                        "updatedAt": "2024-06-16T00:00:00",
                        "livePhotoVideoId": None,
                    },
                ],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30))

        result = await svc.get_photos_for_date_range(dr)
        assert len(result) == 1
        assert result[0].id == "img1"

    @pytest.mark.asyncio
    async def test_get_live_photos_multi_person_intersection(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        def make_live(aid):
            return {
                "id": aid,
                "type": "IMAGE",
                "fileCreatedAt": "2024-06-15T00:00:00",
                "fileModifiedAt": "2024-06-15T00:00:00",
                "updatedAt": "2024-06-15T00:00:00",
                "livePhotoVideoId": "vid",
            }

        resp_p1 = {
            "assets": {
                "total": 2,
                "count": 2,
                "items": [make_live("lp1"), make_live("lp2")],
                "nextPage": None,
            }
        }
        resp_p2 = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [make_live("lp2")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(side_effect=[resp_p1, resp_p2])
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 6, 1), end=datetime(2024, 6, 30))

        result = await svc.get_live_photos_for_date_range(dr, person_ids=["p1", "p2"])
        assert len(result) == 1
        assert result[0].id == "lp2"


class TestSearchServiceIterators:
    """Test async iterator methods."""

    @pytest.mark.asyncio
    async def test_iter_videos_for_date_range(self):
        from immich_memories.api.search_service import SearchService
        from immich_memories.timeperiod import DateRange

        page1 = {
            "assets": {
                "total": 2,
                "count": 1,
                "items": [_make_asset("a1", "2024-01-15T00:00:00")],
                "nextPage": "2",
            }
        }
        page2 = {
            "assets": {
                "total": 2,
                "count": 1,
                "items": [_make_asset("a2", "2024-06-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(side_effect=[page1, page2])
        svc = SearchService(mock_request)
        dr = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31))

        assets = []
        async for asset in svc.iter_videos_for_date_range(dr, batch_size=1):
            assets.append(asset)
        assert len(assets) == 2

    @pytest.mark.asyncio
    async def test_iter_person_videos(self):
        from immich_memories.api.search_service import SearchService

        response = {
            "assets": {
                "total": 1,
                "count": 1,
                "items": [_make_asset("a1", "2024-03-15T00:00:00")],
                "nextPage": None,
            }
        }
        mock_request = AsyncMock(return_value=response)
        svc = SearchService(mock_request)

        assets = []
        async for asset in svc.iter_person_videos("p1", 2024, batch_size=10):
            assets.append(asset)
        assert len(assets) == 1
