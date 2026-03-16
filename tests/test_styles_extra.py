"""Additional tests for title styles — font path resolution and mood styling."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.titles.styles import (
    MOOD_STYLE_PROFILES,
    TitleStyle,
    get_style_for_mood,
)


class TestGetFontPath:
    """Tests for TitleStyle.get_font_path font resolution."""

    def test_returns_none_when_font_missing(self, tmp_path: Path):
        """Returns None when font file does not exist."""
        style = TitleStyle(font_family="Nonexistent", font_weight="medium")
        assert style.get_font_path(fonts_dir=tmp_path) is None

    def test_finds_ttf_font(self, tmp_path: Path):
        """Finds .ttf font in the expected path."""
        font_dir = tmp_path / "Outfit"
        font_dir.mkdir()
        font_file = font_dir / "Outfit-Medium.ttf"
        font_file.write_bytes(b"fake-font")

        style = TitleStyle(font_family="Outfit", font_weight="medium")
        assert style.get_font_path(fonts_dir=tmp_path) == font_file

    def test_finds_otf_fallback(self, tmp_path: Path):
        """Falls back to .otf when .ttf not found."""
        font_dir = tmp_path / "Raleway"
        font_dir.mkdir()
        otf_file = font_dir / "Raleway-Light.otf"
        otf_file.write_bytes(b"fake-font")

        style = TitleStyle(font_family="Raleway", font_weight="light")
        assert style.get_font_path(fonts_dir=tmp_path) == otf_file

    def test_finds_flat_directory_fallback(self, tmp_path: Path):
        """Falls back to font file in flat directory (no family subfolder)."""
        font_file = tmp_path / "Outfit-SemiBold.ttf"
        font_file.write_bytes(b"fake-font")

        style = TitleStyle(font_family="Outfit", font_weight="semibold")
        assert style.get_font_path(fonts_dir=tmp_path) == font_file

    @pytest.mark.parametrize(
        "weight,expected_suffix",
        [
            pytest.param("light", "Light", id="light"),
            pytest.param("regular", "Regular", id="regular"),
            pytest.param("medium", "Medium", id="medium"),
            pytest.param("semibold", "SemiBold", id="semibold"),
        ],
    )
    def test_weight_mapping(self, tmp_path: Path, weight: str, expected_suffix: str):
        """Each weight maps to the correct filename suffix."""
        font_dir = tmp_path / "TestFont"
        font_dir.mkdir()
        font_file = font_dir / f"TestFont-{expected_suffix}.ttf"
        font_file.write_bytes(b"fake")

        style = TitleStyle(font_family="TestFont", font_weight=weight)
        assert style.get_font_path(fonts_dir=tmp_path) == font_file


class TestGetStyleForMood:
    """Tests for mood-to-style mapping."""

    def test_deterministic_without_randomize(self):
        """Without randomization, same mood always gives same style."""
        s1 = get_style_for_mood("happy", randomize=False)
        s2 = get_style_for_mood("happy", randomize=False)
        assert s1.font_family == s2.font_family
        assert s1.text_color == s2.text_color
        assert s1.background_colors == s2.background_colors

    def test_unknown_mood_falls_back_to_default(self):
        """Unknown mood uses the 'default' profile."""
        style = get_style_for_mood("unknown_mood", randomize=False)
        default_profile = MOOD_STYLE_PROFILES["default"]
        assert style.font_weight == default_profile["font_weight"]
        assert style.animation_preset == default_profile["animation_preset"]

    @pytest.mark.parametrize("mood", list(MOOD_STYLE_PROFILES.keys()))
    def test_all_moods_return_valid_style(self, mood: str):
        """Every defined mood returns a TitleStyle with valid fields."""
        style = get_style_for_mood(mood, randomize=False)
        assert isinstance(style, TitleStyle)
        assert style.name == f"{mood}_style"
        assert len(style.background_colors) == 2
        assert style.text_color.startswith("#")

    def test_nostalgic_uses_vignette(self):
        """Nostalgic mood uses vignette background."""
        style = get_style_for_mood("nostalgic", randomize=False)
        assert style.background_type == "vignette"

    def test_calm_disables_line_accent(self):
        """Calm mood disables line accent."""
        style = get_style_for_mood("calm", randomize=False)
        assert not style.use_line_accent
