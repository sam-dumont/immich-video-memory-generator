"""Recurring occasions name the day, not the span between search windows."""

from datetime import date

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, _build_title_settings
from immich_memories.titles.text_builder import generate_title, infer_selection_type


def test_on_this_day_opening_is_one_day_across_years():
    start, end = date(2020, 6, 15), date(2024, 6, 15)
    selection = infer_selection_type(memory_type="on_this_day", start_date=start, end_date=end)
    title = generate_title(selection, start_date=start, end_date=end)
    assert (title.main_title, title.subtitle) == ("June 15", "Through the Years")


def test_holiday_opening_names_the_occasion(tmp_path):
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "holiday.mp4",
        config=Config(),
        memory_type="holiday",
        memory_preset_params={"holiday": "christmas"},
        date_start=date(2020, 12, 23),
        date_end=date(2024, 12, 27),
    )
    settings = _build_title_settings(params, params.config, [])
    assert settings is not None
    assert settings.title_override == "Christmas"
    assert settings.subtitle_override == "Through the Years"

    params.title, params.subtitle = "Our Christmases", "2020–2024"
    settings = _build_title_settings(params, params.config, [])
    assert settings.title_override == "Our Christmases"
    assert settings.subtitle_override == "2020–2024"


def test_a_holiday_that_lost_its_parameter_is_not_called_christmas(tmp_path):
    """No occasion beats the wrong occasion on the one screen every viewer sees."""
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "holiday.mp4",
        config=Config(),
        memory_type="holiday",
        memory_preset_params={},
        date_start=date(2020, 12, 23),
        date_end=date(2024, 12, 27),
    )

    settings = _build_title_settings(params, params.config, [])

    assert settings is not None
    assert settings.title_override is None
