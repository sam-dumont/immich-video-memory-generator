"""Tests for title screens module."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from immich_memories.titles.animations import (
    EASING_FUNCTIONS,
    TEXT_ANIMATIONS,
    apply_easing,
    get_animation_preset,
)
from immich_memories.titles.backgrounds import (
    create_gradient_background,
)
from immich_memories.titles.colors import (
    brighten_color,
    ensure_minimum_brightness,
    hex_to_rgb,
    rgb_to_hex,
)
from immich_memories.titles.generator import (
    ORIENTATION_RESOLUTIONS,
    TitleScreenConfig,
    TitleScreenGenerator,
    get_resolution_for_orientation,
)
from immich_memories.titles.styles import (
    COLOR_PALETTES,
    FONT_STACK,
    PRESET_STYLES,
    TitleStyle,
    get_random_style,
    get_style_for_mood,
)
from immich_memories.titles.text_builder import (
    SelectionType,
    generate_month_divider_text,
    generate_title,
    get_month_name,
    get_ordinal,
    infer_selection_type,
)


class TestTitleStyle:
    """Tests for TitleStyle dataclass."""

    def test_default_style(self):
        """Test default style values."""
        style = TitleStyle(name="test")
        assert style.name == "test"
        assert style.font_family == "Outfit"
        assert style.font_weight == "medium"
        assert style.title_size_ratio == 0.1

    def test_preset_styles_exist(self):
        """Test that preset styles are defined."""
        assert "modern_warm" in PRESET_STYLES
        assert "elegant_minimal" in PRESET_STYLES
        assert "vintage_charm" in PRESET_STYLES
        assert "playful_bright" in PRESET_STYLES
        assert "soft_romantic" in PRESET_STYLES

    def test_color_palettes_exist(self):
        """Test that color palettes are defined."""
        assert COLOR_PALETTES
        for name, palette in COLOR_PALETTES.items():
            assert len(palette) >= 2, f"Palette {name} should have at least 2 colors"

    def test_font_stack_exists(self):
        """Test that font stack is defined."""
        assert FONT_STACK
        assert "Outfit" in FONT_STACK


class TestStyleSelection:
    """Tests for style selection functions."""

    def test_get_random_style(self):
        """Test random style selection."""
        style = get_random_style()
        assert isinstance(style, TitleStyle)
        assert style.name in PRESET_STYLES

    def test_get_style_for_mood_happy(self):
        """Test style selection for happy mood."""
        style = get_style_for_mood("happy")
        assert isinstance(style, TitleStyle)

    def test_get_style_for_mood_calm(self):
        """Test style selection for calm mood."""
        style = get_style_for_mood("calm")
        assert isinstance(style, TitleStyle)

    def test_get_style_for_unknown_mood(self):
        """Test style selection for unknown mood falls back to random."""
        style = get_style_for_mood("unknown_mood_xyz")
        assert isinstance(style, TitleStyle)


class TestTextBuilder:
    """Tests for title text generation."""

    def test_infer_selection_type_calendar_year(self):
        """Test selection type inference for calendar year."""
        sel_type = infer_selection_type(year=2024)
        assert sel_type == SelectionType.CALENDAR_YEAR

    def test_infer_selection_type_birthday_year(self):
        """Test selection type inference for birthday year."""
        sel_type = infer_selection_type(year=2024, birthday_age=1)
        assert sel_type == SelectionType.BIRTHDAY_YEAR

    def test_infer_selection_type_single_month(self):
        """Test selection type inference for single month."""
        sel_type = infer_selection_type(year=2024, month=6)
        assert sel_type == SelectionType.SINGLE_MONTH

    def test_infer_selection_type_date_range(self):
        """Test selection type inference for date range."""
        sel_type = infer_selection_type(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
        )
        assert sel_type == SelectionType.DATE_RANGE

    def test_generate_title_calendar_year_en(self):
        """Test title generation for calendar year in English."""
        title_info = generate_title(
            SelectionType.CALENDAR_YEAR,
            year=2024,
            locale="en",
        )
        assert title_info.main_title == "2024"
        assert title_info.subtitle is None

    def test_generate_title_birthday_year_en(self):
        """Test title generation for birthday year in English."""
        title_info = generate_title(
            SelectionType.BIRTHDAY_YEAR,
            year=2024,
            birthday_age=1,
            person_name="Emma",
            locale="en",
        )
        assert "Year" in title_info.main_title
        assert title_info.subtitle == "Emma"

    def test_generate_title_birthday_year_fr(self):
        """Test title generation for birthday year in French."""
        title_info = generate_title(
            SelectionType.BIRTHDAY_YEAR,
            year=2024,
            birthday_age=1,
            person_name="Emma",
            locale="fr",
        )
        assert "Année" in title_info.main_title
        assert title_info.subtitle == "Emma"

    def test_generate_title_single_month_en(self):
        """Test title generation for single month in English."""
        title_info = generate_title(
            SelectionType.SINGLE_MONTH,
            year=2024,
            month=6,
            locale="en",
        )
        assert "June" in title_info.main_title
        assert "2024" in title_info.main_title

    def test_generate_title_single_month_fr(self):
        """Test title generation for single month in French."""
        title_info = generate_title(
            SelectionType.SINGLE_MONTH,
            year=2024,
            month=6,
            locale="fr",
        )
        assert "Juin" in title_info.main_title or "juin" in title_info.main_title

    def test_get_month_name_en(self):
        """Test month name in English."""
        assert get_month_name(1, "en") == "January"
        assert get_month_name(12, "en") == "December"

    def test_get_month_name_fr(self):
        """Test month name in French."""
        assert get_month_name(1, "fr").lower() == "janvier"
        assert get_month_name(12, "fr").lower() == "décembre"

    def test_get_ordinal_en(self):
        """Test ordinal numbers in English."""
        assert get_ordinal(1, "en") == "1st"
        assert get_ordinal(2, "en") == "2nd"
        assert get_ordinal(3, "en") == "3rd"
        assert get_ordinal(4, "en") == "4th"

    def test_get_ordinal_fr(self):
        """Test ordinal numbers in French."""
        assert get_ordinal(1, "fr") in ("1er", "1ère", "1ère")
        assert "2" in get_ordinal(2, "fr")

    def test_generate_month_divider_text_en(self):
        """Test month divider text generation in English."""
        text = generate_month_divider_text(6, year=2024, locale="en")
        assert "June" in text

    def test_generate_month_divider_text_fr(self):
        """Test month divider text generation in French."""
        text = generate_month_divider_text(6, year=2024, locale="fr")
        assert "Juin" in text or "juin" in text


class TestOrientationResolutions:
    """Tests for orientation and resolution handling."""

    def test_orientation_resolutions_defined(self):
        """Test that all orientations have resolutions."""
        assert "landscape" in ORIENTATION_RESOLUTIONS
        assert "portrait" in ORIENTATION_RESOLUTIONS
        assert "square" in ORIENTATION_RESOLUTIONS

    def test_resolution_tiers_defined(self):
        """Test that all resolution tiers are defined."""
        for orientation in ("landscape", "portrait", "square"):
            assert "720p" in ORIENTATION_RESOLUTIONS[orientation]
            assert "1080p" in ORIENTATION_RESOLUTIONS[orientation]
            assert "4k" in ORIENTATION_RESOLUTIONS[orientation]

    def test_get_resolution_for_orientation_landscape(self):
        """Test resolution lookup for landscape."""
        res = get_resolution_for_orientation("landscape", "1080p")
        assert res == (1920, 1080)

    def test_get_resolution_for_orientation_portrait(self):
        """Test resolution lookup for portrait."""
        res = get_resolution_for_orientation("portrait", "1080p")
        assert res == (1080, 1920)

    def test_get_resolution_for_orientation_square(self):
        """Test resolution lookup for square."""
        res = get_resolution_for_orientation("square", "1080p")
        assert res == (1080, 1080)

    def test_get_resolution_for_orientation_fallback(self):
        """Test resolution lookup with invalid values falls back."""
        res = get_resolution_for_orientation("invalid", "invalid")
        assert res == (1920, 1080)  # Default


class TestTitleScreenConfig:
    """Tests for TitleScreenConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = TitleScreenConfig()
        assert config.enabled
        assert config.title_duration == 3.5
        assert config.month_divider_duration == 2.0
        assert config.ending_duration == 7.0
        assert config.locale == "en"
        assert config.orientation == "landscape"
        assert config.resolution == "1080p"

    def test_output_resolution_property(self):
        """Test output resolution property."""
        config = TitleScreenConfig(orientation="portrait", resolution="720p")
        assert config.output_resolution == (720, 1280)


class TestAnimations:
    """Tests for animations module."""

    def test_text_animations_defined(self):
        """Test that text animations are defined."""
        assert TEXT_ANIMATIONS
        assert "fade_up" in TEXT_ANIMATIONS
        assert "slow_fade" in TEXT_ANIMATIONS

    def test_easing_functions_defined(self):
        """Test that easing functions are defined."""
        assert EASING_FUNCTIONS
        assert "linear" in EASING_FUNCTIONS
        assert "ease_out_quad" in EASING_FUNCTIONS

    def test_apply_easing_linear(self):
        """Test linear easing."""
        result = apply_easing(0.5, "linear")
        assert result == 0.5

    def test_apply_easing_bounds(self):
        """Test easing at boundaries."""
        for easing_name in EASING_FUNCTIONS:
            result_0 = apply_easing(0.0, easing_name)
            result_1 = apply_easing(1.0, easing_name)
            assert 0.0 <= result_0 <= 0.1, f"{easing_name} at 0"
            assert 0.9 <= result_1 <= 1.0, f"{easing_name} at 1"

    def test_get_animation_preset(self):
        """Test getting animation presets."""
        preset = get_animation_preset("fade_up")
        assert preset is not None
        assert hasattr(preset, "easing")


class TestColors:
    """Tests for color utilities."""

    def test_hex_to_rgb(self):
        """Test hex to RGB conversion."""
        assert hex_to_rgb("#FFFFFF") == (255, 255, 255)
        assert hex_to_rgb("#000000") == (0, 0, 0)
        assert hex_to_rgb("#FF0000") == (255, 0, 0)
        assert hex_to_rgb("#00FF00") == (0, 255, 0)
        assert hex_to_rgb("#0000FF") == (0, 0, 255)

    def test_rgb_to_hex(self):
        """Test RGB to hex conversion."""
        assert rgb_to_hex((255, 255, 255)).upper() == "#FFFFFF"
        assert rgb_to_hex((0, 0, 0)).upper() == "#000000"

    def test_brighten_color(self):
        """Test color brightening."""
        original = (100, 100, 100)
        brightened = brighten_color(original, factor=1.5)
        assert brightened[0] > original[0]
        assert brightened[1] > original[1]
        assert brightened[2] > original[2]

    def test_brighten_color_clamp(self):
        """Test that brightening clamps to 255."""
        original = (200, 200, 200)
        brightened = brighten_color(original, factor=2.0)
        assert brightened[0] <= 255
        assert brightened[1] <= 255
        assert brightened[2] <= 255

    def test_ensure_minimum_brightness(self):
        """Test minimum brightness enforcement."""
        dark_color = (50, 50, 50)
        brightened = ensure_minimum_brightness(dark_color, min_brightness=100)
        # Should be brighter than original
        assert sum(brightened) >= sum(dark_color)


class TestBackgrounds:
    """Tests for background generation."""

    def test_create_gradient_background(self):
        """Test gradient background creation."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL not available")

        bg = create_gradient_background(
            width=100,
            height=100,
            colors=["#FFC896", "#C896FF"],  # Hex colors instead of RGB tuples
        )
        assert isinstance(bg, Image.Image)
        assert bg.size == (100, 100)


class TestTitleScreenGenerator:
    """Tests for TitleScreenGenerator class."""

    def test_generator_initialization(self):
        """Test generator initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(locale="en")
            generator = TitleScreenGenerator(
                config=config,
                output_dir=Path(tmpdir),
            )
            assert generator.config.locale == "en"
            assert generator.output_dir.exists()

    def test_generator_style_selection_auto(self):
        """Test automatic style selection based on mood."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(style_mode="auto")
            generator = TitleScreenGenerator(
                config=config,
                mood="happy",
                output_dir=Path(tmpdir),
            )
            assert generator.style is not None

    def test_generator_style_selection_random(self):
        """Test random style selection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TitleScreenConfig(style_mode="random")
            generator = TitleScreenGenerator(
                config=config,
                output_dir=Path(tmpdir),
            )
            assert generator.style is not None


class TestAssemblyIntegration:
    """Tests for assembly.py integration with title screens."""

    def test_title_screen_settings_dataclass(self):
        """Test TitleScreenSettings dataclass."""
        from immich_memories.processing.assembly_config import TitleScreenSettings

        settings = TitleScreenSettings(
            year=2024,
            locale="en",
            show_month_dividers=True,
        )
        assert settings.year == 2024
        assert settings.locale == "en"
        assert settings.show_month_dividers
        assert settings.enabled

    def test_assembly_settings_with_titles(self):
        """Test AssemblySettings with title screens."""
        from immich_memories.processing.assembly_config import (
            AssemblySettings,
            TitleScreenSettings,
            TransitionType,
        )

        title_settings = TitleScreenSettings(year=2024)
        settings = AssemblySettings(
            transition=TransitionType.CROSSFADE,
            title_screens=title_settings,
        )
        assert settings.title_screens is not None
        assert settings.title_screens.year == 2024

    def test_video_assembler_parse_clip_date(self):
        """Test date parsing from AssemblyClip."""
        from datetime import date

        from immich_memories.processing.assembly_config import (
            AssemblyClip,
            AssemblySettings,
        )
        from immich_memories.processing.video_assembler import VideoAssembler

        assembler = VideoAssembler(AssemblySettings())

        # Test valid date
        clip = AssemblyClip(
            path=Path("/tmp/test.mp4"),
            duration=5.0,
            date="2024-06-15",
        )
        parsed = assembler.title_inserter.parse_clip_date(clip)
        assert parsed == date(2024, 6, 15)

        # Test None date
        clip_no_date = AssemblyClip(
            path=Path("/tmp/test.mp4"),
            duration=5.0,
            date=None,
        )
        assert assembler.title_inserter.parse_clip_date(clip_no_date) is None

    def test_video_assembler_detect_month_changes(self):
        """Test month change detection."""
        from immich_memories.processing.assembly_config import (
            AssemblyClip,
            AssemblySettings,
        )
        from immich_memories.processing.video_assembler import VideoAssembler

        assembler = VideoAssembler(AssemblySettings())

        clips = [
            AssemblyClip(path=Path("/tmp/1.mp4"), duration=5.0, date="2024-01-15"),
            AssemblyClip(path=Path("/tmp/2.mp4"), duration=5.0, date="2024-01-20"),
            AssemblyClip(path=Path("/tmp/3.mp4"), duration=5.0, date="2024-02-10"),
            AssemblyClip(path=Path("/tmp/4.mp4"), duration=5.0, date="2024-02-15"),
            AssemblyClip(path=Path("/tmp/5.mp4"), duration=5.0, date="2024-03-01"),
        ]

        changes = assembler.title_inserter.detect_month_changes(clips)

        # Should detect first month and subsequent month changes
        assert len(changes) == 3
        assert (0, 1, 2024) in changes  # January at index 0 (first month)
        assert (2, 2, 2024) in changes  # February at index 2
        assert (4, 3, 2024) in changes  # March at index 4
