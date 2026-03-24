"""Unit tests for TitleScreenGenerator orchestration logic.

Tests the WIRING — does the generator pass correct arguments to its
composed services? Mocks the rendering boundary (FFmpeg subprocess).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.titles.generator import (
    GeneratedScreen,
    TitleScreenConfig,
    TitleScreenGenerator,
)


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "titles"


@pytest.fixture
def mock_rendering():
    """Mock RenderingService — replaces FFmpeg subprocess calls.

    # WHY: RenderingService.__init__ tries to import taichi and init GPU.
    # We mock the entire class to avoid GPU detection in unit tests.
    """
    with patch("immich_memories.titles.generator.RenderingService") as cls:
        instance = cls.return_value
        instance.create_title_video.return_value = Path("/fake/title.mp4")
        instance.use_gpu = False
        yield instance


@pytest.fixture
def mock_ending():
    """Mock EndingService — replaces FFmpeg frame-piping for endings.

    # WHY: EndingService.create_ending_video streams PIL frames to FFmpeg
    # subprocess. We mock it to test orchestration without FFmpeg.
    """
    with patch("immich_memories.titles.generator.EndingService") as cls:
        instance = cls.return_value
        yield instance


@pytest.fixture
def mock_trip():
    """Mock TripService — replaces map rendering and satellite tile fetching.

    # WHY: TripService imports map_renderer/map_animation which need
    # cartopy or network access for satellite tiles.
    """
    with patch("immich_memories.titles.generator.TripService") as cls:
        instance = cls.return_value
        yield instance


def _make_generator(
    tmp_output: Path,
    mock_rendering,
    mock_ending,
    mock_trip,
    **config_kwargs,
) -> TitleScreenGenerator:
    """Create a TitleScreenGenerator with mocked services."""
    config = TitleScreenConfig(**config_kwargs)
    return TitleScreenGenerator(config=config, output_dir=tmp_output)


class TestGenerateTitleScreen:
    """Tests that generate_title_screen passes correct args to RenderingService."""

    def test_passes_correct_resolution(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            orientation="portrait",
            resolution="720p",
        )
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["width"] == 720
        assert call_kwargs.kwargs["height"] == 1280

    def test_passes_correct_duration(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            title_duration=5.0,
        )
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["duration"] == 5.0

    def test_passes_correct_fps(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            fps=24.0,
        )
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["fps"] == 24.0

    def test_uses_title_override(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            title_override="Custom Title",
            subtitle_override="Custom Sub",
        )
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["title"] == "Custom Title"
        assert call_kwargs.kwargs["subtitle"] == "Custom Sub"

    def test_without_override_uses_template(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["title"] == "2024"

    def test_infers_selection_type_birthday(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_title_screen(year=2024, birthday_age=3, person_name="Child")

        call_kwargs = mock_rendering.create_title_video.call_args
        assert "Year" in call_kwargs.kwargs["title"]
        assert call_kwargs.kwargs["subtitle"] == "Child"

    def test_fade_from_white_on_title(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_title_screen(year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["fade_from_white"] is True

    def test_content_backed_passes_clip_path(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "content_backed"
        clip = Path("/fake/clip.mp4")

        gen.generate_title_screen(year=2024, content_clip_path=clip)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["content_clip_path"] == clip

    def test_non_content_backed_no_clip_path(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "soft_gradient"
        clip = Path("/fake/clip.mp4")

        gen.generate_title_screen(year=2024, content_clip_path=clip)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs.get("content_clip_path") is None

    def test_returns_generated_screen(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        result = gen.generate_title_screen(year=2024)

        assert isinstance(result, GeneratedScreen)
        assert result.screen_type == "title"
        assert result.duration == 3.5
        assert result.path == tmp_output / "title_screen.mp4"


class TestGenerateMonthDivider:
    """Tests that month dividers get correct style and text."""

    def test_divider_style_lighter_weight(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_month_divider(month=6)

        call_kwargs = mock_rendering.create_title_video.call_args
        style = call_kwargs.kwargs["style"]
        assert style.font_weight == "medium"
        assert style.title_size_ratio == 0.10

    def test_divider_uses_correct_month_text(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_month_divider(month=6, year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert "June" in call_kwargs.kwargs["title"]

    def test_divider_uses_locale(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            locale="fr",
        )
        gen.generate_month_divider(month=6, year=2024)

        call_kwargs = mock_rendering.create_title_video.call_args
        title = call_kwargs.kwargs["title"]
        assert "Juin" in title or "juin" in title

    def test_birthday_month_adds_celebration(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_month_divider(month=3, is_birthday_month=True)

        call_kwargs = mock_rendering.create_title_video.call_args
        style = call_kwargs.kwargs["style"]
        assert style.animation_preset == "bounce_in"
        assert call_kwargs.kwargs["subtitle"] is not None
        assert call_kwargs.kwargs["is_birthday"] is True

    def test_normal_month_no_birthday(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_month_divider(month=3, is_birthday_month=False)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["subtitle"] is None
        assert call_kwargs.kwargs.get("is_birthday", False) is False

    def test_divider_uses_config_duration(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            month_divider_duration=3.0,
        )
        gen.generate_month_divider(month=1)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["duration"] == 3.0

    def test_returns_month_divider_screen(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        result = gen.generate_month_divider(month=6)

        assert result.screen_type == "month_divider"
        assert "month_divider_06" in result.path.name


class TestGenerateYearDivider:
    def test_year_divider_uses_year_text(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_year_divider(year=2023)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["title"] == "2023"
        assert call_kwargs.kwargs["subtitle"] is None

    def test_year_divider_light_weight(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.generate_year_divider(year=2023)

        call_kwargs = mock_rendering.create_title_video.call_args
        style = call_kwargs.kwargs["style"]
        assert style.font_weight == "light"


class TestGenerateEndingScreen:
    """Tests that ending screen routes to correct service."""

    def test_gradient_ending_uses_ending_service(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "soft_gradient"
        gen.generate_ending_screen()

        mock_ending.create_ending_video.assert_called_once()
        mock_rendering.create_title_video.assert_not_called()

    def test_gradient_ending_passes_white_fade(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "soft_gradient"
        gen.generate_ending_screen()

        call_kwargs = mock_ending.create_ending_video.call_args
        assert call_kwargs.kwargs["fade_to_color"] == (255, 255, 255)

    def test_content_backed_ending_uses_rendering_service(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "content_backed"
        clip = Path("/fake/last_clip.mp4")

        gen.generate_ending_screen(content_clip_path=clip)

        mock_rendering.create_title_video.assert_called_once()
        mock_ending.create_ending_video.assert_not_called()

    def test_content_backed_ending_sets_reverse_flags(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "content_backed"
        clip = Path("/fake/last_clip.mp4")

        gen.generate_ending_screen(content_clip_path=clip)

        call_kwargs = mock_rendering.create_title_video.call_args
        assert call_kwargs.kwargs["fade_to_white"] is True
        assert call_kwargs.kwargs["is_ending"] is True
        assert call_kwargs.kwargs["title"] == ""

    def test_content_backed_no_clip_falls_through_to_ending_service(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        """content_backed style but no clip path -> should use EndingService fallback."""
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        gen.style.background_type = "content_backed"

        gen.generate_ending_screen(content_clip_path=None)

        # WHY: Without a clip, content_backed can't do reverse slow-mo.
        # Should fall through to EndingService (fade to white).
        mock_ending.create_ending_video.assert_called_once()
        mock_rendering.create_title_video.assert_not_called()

    def test_ending_uses_config_duration(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            ending_duration=6.0,
        )
        gen.style.background_type = "soft_gradient"
        gen.generate_ending_screen()

        call_kwargs = mock_ending.create_ending_video.call_args
        assert call_kwargs.kwargs["duration"] == 6.0

    def test_returns_ending_screen(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        result = gen.generate_ending_screen()

        assert result.screen_type == "ending"
        assert result.path.name == "ending_screen.mp4"


class TestGenerateAllScreens:
    """Tests for the batch screen generation orchestrator."""

    def test_always_produces_title_and_ending(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        screens = gen.generate_all_screens(year=2024)

        assert "title" in screens
        assert "ending" in screens
        assert screens["title"].screen_type == "title"
        assert screens["ending"].screen_type == "ending"

    def test_multiple_months_produce_dividers(
        self, tmp_output, mock_rendering, mock_ending, mock_trip
    ):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        screens = gen.generate_all_screens(
            year=2024,
            months_in_video=[1, 3, 6],
        )

        assert "month_01" in screens
        assert "month_03" in screens
        assert "month_06" in screens
        assert len(screens) == 5  # title + 3 dividers + ending

    def test_single_month_no_dividers(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        screens = gen.generate_all_screens(
            year=2024,
            months_in_video=[6],
        )

        assert len(screens) == 2  # title + ending only

    def test_dividers_disabled(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(
            tmp_output,
            mock_rendering,
            mock_ending,
            mock_trip,
            show_month_dividers=False,
        )
        screens = gen.generate_all_screens(
            year=2024,
            months_in_video=[1, 3, 6],
        )

        assert len(screens) == 2  # title + ending only

    def test_no_months_list_no_dividers(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        gen = _make_generator(tmp_output, mock_rendering, mock_ending, mock_trip)
        screens = gen.generate_all_screens(year=2024, months_in_video=None)

        assert len(screens) == 2  # title + ending only


class TestStyleSelection:
    """Tests that style selection works correctly for all modes."""

    def test_mood_based_style(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        with (
            patch("immich_memories.titles.generator.RenderingService"),
            patch("immich_memories.titles.generator.EndingService"),
            patch("immich_memories.titles.generator.TripService"),
        ):
            gen = TitleScreenGenerator(
                config=TitleScreenConfig(style_mode="auto"),
                mood="happy",
                output_dir=tmp_output,
            )
        assert gen.style is not None
        assert gen.style.name != "default"

    def test_named_style(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        with (
            patch("immich_memories.titles.generator.RenderingService"),
            patch("immich_memories.titles.generator.EndingService"),
            patch("immich_memories.titles.generator.TripService"),
        ):
            gen = TitleScreenGenerator(
                config=TitleScreenConfig(style_mode="modern_warm"),
                output_dir=tmp_output,
            )
        assert gen.style.name == "modern_warm"

    def test_decorative_lines_disabled(self, tmp_output, mock_rendering, mock_ending, mock_trip):
        with (
            patch("immich_memories.titles.generator.RenderingService"),
            patch("immich_memories.titles.generator.EndingService"),
            patch("immich_memories.titles.generator.TripService"),
        ):
            gen = TitleScreenGenerator(
                config=TitleScreenConfig(show_decorative_lines=False),
                output_dir=tmp_output,
            )
        assert gen.style.use_line_accent is False
