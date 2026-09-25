"""A film whose window is exactly a season is titled by the season, in the home's hemisphere."""

from __future__ import annotations

from datetime import date

import pytest

from immich_memories.titles.text_builder import SelectionType, generate_title


def _title(start: date, end: date, hemisphere: str | None, locale: str = "en") -> str:
    return generate_title(
        SelectionType.DATE_RANGE, start_date=start, end_date=end, locale=locale,
        hemisphere=hemisphere,
    ).main_title  # fmt: skip


def test_a_northern_summer_is_called_summer():
    assert _title(date(2025, 6, 1), date(2025, 8, 31), "north") == "Summer 2025"


def test_a_northern_winter_spans_its_two_years():
    assert _title(date(2024, 12, 1), date(2025, 2, 28), "north") == "Winter 2024–25"


def test_december_to_february_is_summer_in_the_south():
    assert _title(date(2024, 12, 1), date(2025, 2, 28), "south") == "Summer 2024–25"
    assert _title(date(2025, 6, 1), date(2025, 8, 31), "south") == "Winter 2025"


def test_a_leap_year_february_still_ends_the_season():
    assert _title(date(2023, 12, 1), date(2024, 2, 29), "north") == "Winter 2023–24"


@pytest.mark.parametrize(
    ("start", "end", "hemisphere", "expected"),
    [
        (date(2025, 6, 1), date(2025, 8, 31), None, "June to August 2025"),  # no home base
        (date(2025, 6, 2), date(2025, 8, 31), "north", "June to August 2025"),  # a day short
        (date(2025, 6, 1), date(2025, 9, 30), "north", "June to September 2025"),  # more
    ],
)
def test_without_a_home_or_an_exact_season_the_months_name_it(start, end, hemisphere, expected):
    assert _title(start, end, hemisphere) == expected


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("fr", "Été 2025"), ("de", "Sommer 2025"), ("ja", "2025年の夏"), ("es", "Verano de 2025")],
)
def test_the_season_is_named_in_the_films_language(locale, expected):
    assert _title(date(2025, 6, 1), date(2025, 8, 31), "north", locale) == expected


def _settings(tmp_path, latitude: float, longitude: float):
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import build_title_settings

    config = Config()
    config.trips.homebase_latitude, config.trips.homebase_longitude = latitude, longitude
    params = GenerationParams(
        clips=[], output_path=tmp_path / "film.mp4", config=config, memory_type="season",
        date_start=date(2024, 12, 1), date_end=date(2025, 2, 28),
    )  # fmt: skip
    return build_title_settings(params, config, [])


def test_the_hemisphere_comes_from_the_home_base(tmp_path):
    assert _settings(tmp_path, -33.87, 151.21).hemisphere == "south"
    assert _settings(tmp_path, 50.85, 4.35).hemisphere == "north"
    assert _settings(tmp_path, 0.0, 0.0).hemisphere is None  # no home base set


def test_the_title_screen_names_the_season_its_home_has(tmp_path, monkeypatch):
    from immich_memories.titles import TitleScreenConfig, TitleScreenGenerator

    drawn: list[str] = []
    generator = TitleScreenGenerator(
        config=TitleScreenConfig(hemisphere="south", use_gpu_rendering=False),
        output_dir=tmp_path,
    )
    # WHY: the encode is the write boundary; this test is about the words handed to it.
    monkeypatch.setattr(
        generator._rendering, "create_title_video", lambda **kw: drawn.append(kw["title"])
    )

    generator.generate_title_screen(start_date=date(2024, 12, 1), end_date=date(2025, 2, 28))

    assert drawn == ["Summer 2024–25"]


def test_the_web_ui_template_names_an_exact_season_too():
    from immich_memories.ui.pages.pipeline_title import generate_template_title

    title, _ = generate_template_title(
        memory_type="custom", start_date="2024-12-01", end_date="2025-02-28", hemisphere="south"
    )

    assert title == "Summer 2024–25"
