"""Tests for cinematic title screen visual properties.

Verifies that title styles produce dark backgrounds with white text,
proper contrast ratios, and cinematic aesthetics rather than pastel gradients.
Tests assert actual visual properties (colors, contrast, luminance) not just
that functions return values.
"""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.titles.backgrounds import (
    BackgroundType,
    create_background_for_style,
    hex_to_rgb,
)
from immich_memories.titles.styles import (
    COLOR_PALETTES,
    MOOD_STYLE_PROFILES,
    PRESET_STYLES,
    TitleStyle,
    get_style_for_mood,
)


def _luminance(rgb: tuple[int, int, int]) -> float:
    """Calculate relative luminance (0=black, 1=white) per WCAG 2.0."""
    r, g, b = [c / 255.0 for c in rgb]
    r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(lum1: float, lum2: float) -> float:
    """Calculate WCAG contrast ratio between two luminances."""
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


class TestDarkPalettes:
    """Verify all palettes produce genuinely dark backgrounds."""

    def test_default_style_has_dark_background(self):
        """Default TitleStyle background colors should be dark (luminance < 0.1)."""
        style = TitleStyle()
        for hex_color in style.background_colors:
            rgb = hex_to_rgb(hex_color)
            lum = _luminance(rgb)
            assert lum < 0.1, (
                f"Default background {hex_color} has luminance {lum:.3f}, "
                f"expected < 0.1 (dark). Got RGB {rgb}"
            )

    def test_default_style_has_white_text(self):
        """Default TitleStyle text color should be white/near-white."""
        style = TitleStyle()
        rgb = hex_to_rgb(style.text_color)
        lum = _luminance(rgb)
        assert lum > 0.8, (
            f"Default text color {style.text_color} has luminance {lum:.3f}, "
            f"expected > 0.8 (white/near-white)"
        )

    def test_default_style_has_sufficient_contrast(self):
        """Default style must have WCAG AA contrast ratio >= 4.5:1."""
        style = TitleStyle()
        text_lum = _luminance(hex_to_rgb(style.text_color))
        bg_lum = _luminance(hex_to_rgb(style.background_colors[0]))
        ratio = _contrast_ratio(text_lum, bg_lum)
        assert ratio >= 4.5, (
            f"Contrast ratio {ratio:.1f}:1 is below WCAG AA minimum 4.5:1. "
            f"Text: {style.text_color}, BG: {style.background_colors[0]}"
        )

    @pytest.mark.parametrize("palette_name", list(COLOR_PALETTES.keys()))
    def test_all_palettes_have_adequate_contrast(self, palette_name: str):
        """Every palette should produce text/bg combinations with >= 4.5:1 contrast."""
        palette = COLOR_PALETTES[palette_name]
        # Skip legacy palettes that may have light backgrounds
        if palette_name.startswith("_legacy_"):
            pytest.skip("Legacy palette — backwards compat only")
        for bg_pair in palette["backgrounds"]:
            for text_color in palette["text_colors"]:
                text_lum = _luminance(hex_to_rgb(text_color))
                bg_lum = _luminance(hex_to_rgb(bg_pair[0]))
                ratio = _contrast_ratio(text_lum, bg_lum)
                assert ratio >= 4.5, (
                    f"Palette '{palette_name}': contrast {ratio:.1f}:1 "
                    f"(text={text_color}, bg={bg_pair[0]}) is below 4.5:1"
                )


class TestMoodStylesCinematic:
    """Verify mood profiles produce cinematic (dark bg, white text) styles."""

    @pytest.mark.parametrize("mood", list(MOOD_STYLE_PROFILES.keys()))
    def test_mood_produces_dark_background(self, mood: str):
        """Every mood's style should have dark background colors."""
        style = get_style_for_mood(mood, randomize=False)
        for hex_color in style.background_colors:
            rgb = hex_to_rgb(hex_color)
            lum = _luminance(rgb)
            assert lum < 0.15, (
                f"Mood '{mood}' background {hex_color} has luminance {lum:.3f}, "
                f"expected < 0.15 (dark)"
            )

    @pytest.mark.parametrize("mood", list(MOOD_STYLE_PROFILES.keys()))
    def test_mood_produces_light_text(self, mood: str):
        """Every mood's style should have light text color."""
        style = get_style_for_mood(mood, randomize=False)
        rgb = hex_to_rgb(style.text_color)
        lum = _luminance(rgb)
        assert lum > 0.7, (
            f"Mood '{mood}' text {style.text_color} has luminance {lum:.3f}, "
            f"expected > 0.7 (light text)"
        )

    @pytest.mark.parametrize("mood", list(MOOD_STYLE_PROFILES.keys()))
    def test_mood_disables_line_accents(self, mood: str):
        """No mood should enable decorative line accents (cinematic = minimal)."""
        style = get_style_for_mood(mood, randomize=False)
        assert not style.use_line_accent, (
            f"Mood '{mood}' has use_line_accent=True, "
            f"expected False for cinematic minimal aesthetic"
        )

    @pytest.mark.parametrize("mood", list(MOOD_STYLE_PROFILES.keys()))
    def test_mood_uses_bold_weight(self, mood: str):
        """All moods should use medium or semibold weight (confident typography)."""
        style = get_style_for_mood(mood, randomize=False)
        assert style.font_weight in ("medium", "semibold"), (
            f"Mood '{mood}' uses font_weight='{style.font_weight}', "
            f"expected 'medium' or 'semibold' for confident typography"
        )


class TestPresetStylesCinematic:
    """Verify preset styles are dark and cinematic."""

    @pytest.mark.parametrize("preset_name", list(PRESET_STYLES.keys()))
    def test_preset_has_dark_background(self, preset_name: str):
        """Every preset should have dark background."""
        style = PRESET_STYLES[preset_name]
        for hex_color in style.background_colors:
            rgb = hex_to_rgb(hex_color)
            lum = _luminance(rgb)
            assert lum < 0.15, (
                f"Preset '{preset_name}' background {hex_color} luminance={lum:.3f}, "
                f"expected < 0.15"
            )

    @pytest.mark.parametrize("preset_name", list(PRESET_STYLES.keys()))
    def test_preset_has_light_text(self, preset_name: str):
        """Every preset should have white/light text."""
        style = PRESET_STYLES[preset_name]
        rgb = hex_to_rgb(style.text_color)
        lum = _luminance(rgb)
        assert lum > 0.7, (
            f"Preset '{preset_name}' text {style.text_color} luminance={lum:.3f}, expected > 0.7"
        )

    @pytest.mark.parametrize("preset_name", list(PRESET_STYLES.keys()))
    def test_preset_no_line_accent(self, preset_name: str):
        """No preset should use decorative line accents."""
        style = PRESET_STYLES[preset_name]
        assert not style.use_line_accent, f"Preset '{preset_name}' should not use line accent"


class TestTypographyDefaults:
    """Verify typography defaults are bold and confident."""

    def test_default_title_size_ratio(self):
        """Default title should be 12% of screen height (larger than old 10%)."""
        style = TitleStyle()
        assert style.title_size_ratio >= 0.12, (
            f"title_size_ratio={style.title_size_ratio}, expected >= 0.12"
        )

    def test_default_font_weight_is_semibold(self):
        """Default font weight should be semibold for confident typography."""
        style = TitleStyle()
        assert style.font_weight == "semibold"

    def test_default_blend_mode_is_normal(self):
        """Default blend mode should be 'normal' (multiply is invisible on dark)."""
        style = TitleStyle()
        assert style.text_blend_mode == "normal"

    def test_default_no_line_accent(self):
        """Default style should not use decorative line accents."""
        style = TitleStyle()
        assert not style.use_line_accent

    def test_default_text_shadow_enabled(self):
        """Default style should have text shadow for readability on dark bg."""
        style = TitleStyle()
        assert style.text_shadow


class TestBackgroundGeneration:
    """Verify background generation produces dark outputs."""

    def test_gradient_background_is_dark(self):
        """Generated gradient background should use dark colors."""
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("PIL not available")

        bg = create_background_for_style(
            width=100,
            height=100,
            background_type="solid_gradient",
            colors=["#1A1A2E", "#16213E"],
        )
        arr = np.array(bg)
        mean_brightness = arr.mean() / 255.0
        assert mean_brightness < 0.15, (
            f"Dark gradient background has mean brightness {mean_brightness:.3f}, expected < 0.15"
        )

    def test_vignette_darkens_edges_for_dark_palette(self):
        """Vignette on dark background should darken edges (fade to black, not white)."""
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("PIL not available")

        bg = create_background_for_style(
            width=200,
            height=200,
            background_type="vignette",
            colors=["#1A1A2E", "#16213E"],
        )
        arr = np.array(bg)
        # Edge pixels should be very dark (near black)
        edge_brightness = arr[0, 0].mean() / 255.0  # top-left corner
        center_brightness = arr[100, 100].mean() / 255.0
        assert edge_brightness < center_brightness, (
            f"Vignette edge ({edge_brightness:.3f}) should be darker than "
            f"center ({center_brightness:.3f})"
        )
        assert edge_brightness < 0.15, (
            f"Vignette edge brightness {edge_brightness:.3f} should be < 0.15 (dark)"
        )

    def test_content_backed_in_background_type_enum(self):
        """BackgroundType enum should include CONTENT_BACKED."""
        assert hasattr(BackgroundType, "CONTENT_BACKED")


class TestTitleConfigDefaults:
    """Verify TitleScreenConfig defaults are cinematic."""

    def test_avoid_dark_colors_is_false(self):
        """Config should NOT avoid dark colors (dark is the new default)."""
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig()
        assert not config.avoid_dark_colors

    def test_show_decorative_lines_is_false(self):
        """Config should not show decorative lines by default."""
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig()
        assert not config.show_decorative_lines

    def test_minimum_brightness_is_zero(self):
        """No minimum brightness enforcement (dark colors are intentional)."""
        from immich_memories.titles.generator import TitleScreenConfig

        config = TitleScreenConfig()
        assert config.minimum_brightness == 0
