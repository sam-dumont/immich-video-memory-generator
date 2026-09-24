"""Which source produced a film's title, so a template fallback never passes for the model's."""

from __future__ import annotations

from datetime import date

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, _build_title_settings


def _params(tmp_path, **overrides) -> GenerationParams:
    fields = {
        "clips": [],
        "output_path": tmp_path / "film.mp4",
        "config": Config(),
        "memory_type": "year",
        "date_start": date(2024, 1, 1),
        "date_end": date(2024, 12, 31),
    } | overrides
    return GenerationParams(**fields)


def test_a_film_nobody_named_opens_on_the_template(tmp_path):
    settings = _build_title_settings(_params(tmp_path), Config(), [])

    assert settings is not None
    assert settings.title_source == "fallback"


def test_a_holiday_is_named_by_its_occasion(tmp_path):
    params = _params(tmp_path, memory_type="holiday", memory_preset_params={"holiday": "christmas"})

    settings = _build_title_settings(params, params.config, [])

    assert settings is not None
    assert settings.title_source == "occasion"


def test_a_trip_is_named_by_its_place(tmp_path):
    params = _params(
        tmp_path,
        memory_type="trip",
        memory_preset_params={
            "location_name": "Crete, Greece",
            "trip_start": date(2024, 7, 1),
            "trip_end": date(2024, 7, 14),
        },
    )

    settings = _build_title_settings(params, params.config, [])

    assert settings is not None
    assert settings.title_source == "place"


def test_a_title_the_run_was_handed_keeps_the_source_it_came_with(tmp_path):
    params = _params(tmp_path, title="A summer by the sea", title_source="model")

    settings = _build_title_settings(params, params.config, [])

    assert settings is not None
    assert settings.title_override == "A summer by the sea"
    assert settings.title_source == "model"


def test_a_title_with_no_stated_source_is_an_override(tmp_path):
    params = _params(tmp_path, title="Our year")

    settings = _build_title_settings(params, params.config, [])

    assert settings is not None
    assert settings.title_source == "override"


def _resolve(*, title_override=None, memory_type="year", preset_params=None, ask=None, config=None):
    from datetime import datetime
    from types import SimpleNamespace

    from immich_memories.cli._llm_title import resolve_cli_title
    from immich_memories.timeperiod import DateRange

    def reader(**_kwargs):
        # WHY: the title reader is an LLM call, the one boundary this crosses.
        return SimpleNamespace(title="A summer by the sea", subtitle=None)

    return resolve_cli_title(
        enabled=None,
        title_override=title_override,
        clips=[],
        config=config or Config(),
        memory_type=memory_type,
        date_range=DateRange(start=datetime(2025, 7, 1), end=datetime(2025, 7, 14)),
        person_names=[],
        memory_preset_params=preset_params,
        ask=ask or reader,
    )


def test_a_typed_title_is_an_override():
    assert _resolve(title_override="Our summer")[2] == "override"


def test_an_album_memory_is_named_by_its_album():
    source = _resolve(
        title_override="Road trip 2025",
        memory_type="album",
        preset_params={"album_name": "Road trip 2025"},
    )[2]

    assert source == "album"


def test_a_special_day_is_named_by_its_catalogue():
    source = _resolve(
        title_override="First day of school",
        memory_type="special_day",
        preset_params={"title": "First day of school"},
    )[2]

    assert source == "occasion"


def test_a_title_the_model_wrote_is_the_models():
    config = Config()
    config.llm.model = "some-model"

    title, _subtitle, source = _resolve(memory_type="multi_person", config=config)

    assert (title, source) == ("A summer by the sea", "model")


def test_no_title_yet_leaves_the_source_to_the_template_layers():
    title, _subtitle, source = _resolve()

    assert (title, source) == (None, None)


def test_the_run_logs_and_records_which_source_titled_the_film(tmp_path, caplog):
    import logging

    from immich_memories.generate_settings import announce_title_source
    from immich_memories.processing.assembly_config import TitleScreenSettings
    from immich_memories.tracking import RunTracker

    tracker = RunTracker("20250701_100000_abcd", db_path=tmp_path / "runs.db", capture_system=False)
    tracker.start_run(memory_type="trip")
    settings = TitleScreenSettings(title_source="place", trip_title_text="A WEEK IN CRETE, GREECE")

    with caplog.at_level(logging.INFO, logger="immich_memories.generate_settings"):
        announce_title_source(settings, tracker)

    lines = [r.getMessage() for r in caplog.records if "title" in r.getMessage().lower()]
    assert lines == ["Opening title from place: 'A WEEK IN CRETE, GREECE'"]
    run = tracker.db.get_run(tracker.run_id)
    assert run is not None
    assert run.title_source == "place"


def test_a_template_title_says_so_rather_than_quoting_a_title_it_has_not_built(tmp_path, caplog):
    import logging

    from immich_memories.generate_settings import announce_title_source
    from immich_memories.processing.assembly_config import TitleScreenSettings
    from immich_memories.tracking import RunTracker

    tracker = RunTracker("20250701_100000_abce", db_path=tmp_path / "runs.db", capture_system=False)
    tracker.start_run(memory_type="year")

    with caplog.at_level(logging.INFO, logger="immich_memories.generate_settings"):
        announce_title_source(TitleScreenSettings(), tracker)

    assert "Opening title from fallback: the template" in caplog.text
    assert tracker.db.get_run(tracker.run_id).title_source == "fallback"


def test_runs_show_says_where_the_title_came_from():
    from datetime import UTC, datetime

    from immich_memories.cli._helpers import console
    from immich_memories.cli.runs import _print_run_details_table
    from immich_memories.tracking.models import RunMetadata

    run = RunMetadata(
        run_id="20250701_100000_abcd",
        created_at=datetime(2025, 7, 1, tzinfo=UTC),
        status="completed",
        title_source="model",
    )

    with console.capture() as captured:
        _print_run_details_table(run, lambda seconds: f"{seconds:.0f}s")

    assert "Title From" in captured.get()
    assert "model" in captured.get()
