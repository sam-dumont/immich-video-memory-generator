"""Generating the day the catalogue found.

The flag carries a date, never a title. `runner.py` logs the whole argv, and
argv is readable in `ps` and in launchd's own logs, so the one thing a scheduled
run says out loud is a date; the child re-reads the catalogue for the name.
Every day here is invented -- the real catalogue names real people and places.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from immich_memories.cli.generate_resolution import name_from_catalogue, resolve_special_day
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType

# An invented day, in the style the docs already use. Nothing real belongs here.
DAY_ISO = "2016-06-12"
DAY = date(2016, 6, 12)
TITLE = "An afternoon at the track"
ENTRY = {
    "day": DAY_ISO,
    "title": TITLE,
    "subtitle": "Somebody's first race",
    "what": "A long day out",
    "photos": 210,
    "active_hours": 9,
}


def _configured(tmp_path: Path) -> Path:
    """A config with just enough Immich for `generate` to get past its own gate."""
    path = tmp_path / "config.yaml"
    path.write_text("immich:\n  url: http://immich.invalid\n  api_key: not-a-real-key\n")
    return path


@pytest.fixture
def run_generate(tmp_path: Path):
    """Invoke `generate` the way a person or the runner does: through argv."""
    from immich_memories.cli import main

    config = _configured(tmp_path)

    def _invoke(*args: str):
        # WHY: init_config_dir writes to the real home directory, which a unit
        # test must not touch. Nothing else about the command is replaced.
        with patch("immich_memories.cli.init_config_dir"):
            return CliRunner().invoke(main, ["-c", str(config), "generate", *args])

    return _invoke


class _RecordingImmich:
    """Stands in for the Immich HTTP API, keeping the windows it was asked for."""

    def __init__(self) -> None:
        self.windows: list = []

    def get_videos_for_date_range(self, date_range) -> list:
        self.windows.append(date_range)
        return []

    def __enter__(self) -> _RecordingImmich:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


@pytest.fixture
def windows_asked_for(run_generate, monkeypatch: pytest.MonkeyPatch):
    """Run `generate` to the point of the query, and hand back what it asked for."""

    def _run(*args: str) -> list:
        client = _RecordingImmich()
        # WHY: the Immich HTTP API is the boundary this run would cross. It
        # records the window instead of answering it; every step that decides
        # the window -- catalogue, preset, date resolution -- is the real one.
        monkeypatch.setattr("immich_memories.api.immich.SyncImmichClient", lambda **_kwargs: client)
        run_generate("--no-photos", "--no-live-photos", *args)
        return client.windows

    return _run


def _immich_payload_for(date_range) -> dict:
    """The body Immich actually receives for a window.

    A DateRange is not the contract -- what survives the timezone round trip
    into takenAfter/takenBefore is, and that is where a window recorded with an
    offset either keeps its clock times or silently shifts.
    """
    import asyncio

    from immich_memories.api.search_service import SearchService

    recorded: dict = {}

    # WHY: the same Immich HTTP boundary, one layer lower, so the assertion can
    # be about the request body rather than the object that produced it.
    async def request(_method: str, _path: str, *, json: dict) -> dict:
        recorded.update(json)
        return {}

    asyncio.run(SearchService(request).get_videos_for_date_range(date_range))
    return recorded


@pytest.fixture
def catalogue_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the shared catalogue reader at a file this test wrote.

    The path resolves from the home directory, which is the one thing a test
    has to move; the reader itself is the real one.
    """

    def _install(*entries: dict) -> Path:
        path = tmp_path / "special-days.json"
        path.write_text(json.dumps(list(entries)))
        monkeypatch.setattr(
            "immich_memories.automation.catalogue.default_catalogue_path", lambda: path
        )
        return path

    return _install


class TestFlagPairing:
    """--day and --memory-type special_day mean nothing apart."""

    def test_a_special_day_without_a_day_names_the_flag_it_wants(self, run_generate) -> None:
        result = run_generate("--memory-type", "special_day")

        assert result.exit_code != 0
        assert "--day" in result.output

    def test_a_day_on_any_other_memory_type_is_refused(self, run_generate) -> None:
        """A date scoped to one occasion says nothing about a yearly review."""
        result = run_generate("--memory-type", "year_in_review", "--year", "2016", "--day", DAY_ISO)

        assert result.exit_code != 0
        assert "--day requires --memory-type special_day" in result.output


class TestRefuseOverFake:
    """A day the catalogue cannot name is a day that must not be rendered."""

    def test_a_day_that_is_not_in_the_catalogue_names_the_file_it_looked_in(
        self, run_generate, catalogue_at
    ) -> None:
        path = catalogue_at({**ENTRY, "day": "2019-12-31"})

        result = run_generate("--memory-type", "special_day", "--day", DAY_ISO)

        assert result.exit_code != 0
        assert str(path) in result.output
        assert DAY_ISO in result.output

    def test_a_catalogued_day_with_no_name_at_all_is_refused(
        self, run_generate, catalogue_at
    ) -> None:
        catalogue_at({"day": DAY_ISO, "title": "  ", "what": "", "photos": 210})

        result = run_generate("--memory-type", "special_day", "--day", DAY_ISO)

        assert result.exit_code != 0
        assert "nothing truthful" in result.output


class TestTheWindowIsTheScope:
    """What the catalogue recorded is what Immich gets asked for."""

    def test_a_recorded_window_reaches_immich_with_its_clock_times_intact(
        self, windows_asked_for, catalogue_at
    ) -> None:
        # The catalogue writes the window with the offset Immich returned, and
        # only a window that keeps that offset scopes the memory to the hours
        # the day actually happened in.
        catalogue_at(
            {**ENTRY, "window": ["2016-06-12T13:45:00+02:00", "2016-06-12T16:03:00+02:00"]}
        )

        windows = windows_asked_for("--memory-type", "special_day", "--day", DAY_ISO)

        assert len(windows) == 1
        payload = _immich_payload_for(windows[0])
        assert payload["takenAfter"] == "2016-06-12T11:45:00+00:00"
        assert payload["takenBefore"] == "2016-06-12T14:03:00+00:00"

    def test_a_day_with_no_window_is_scoped_to_the_whole_calendar_day(
        self, windows_asked_for, catalogue_at
    ) -> None:
        catalogue_at(ENTRY)

        windows = windows_asked_for("--memory-type", "special_day", "--day", DAY_ISO)

        payload = _immich_payload_for(windows[0])
        assert payload["takenAfter"] == "2016-06-12T00:00:00+00:00"
        assert payload["takenBefore"] == "2016-06-12T23:59:59+00:00"


class TestTheTitleNeverTravelsOnTheCommandLine:
    """The privacy contract: a date on argv, the name out of the catalogue."""

    def test_a_day_alone_is_enough_for_the_catalogue_to_name_the_memory(
        self, windows_asked_for, catalogue_at
    ) -> None:
        catalogue_at(ENTRY)
        argv = ("--memory-type", "special_day", "--day", DAY_ISO)

        assert windows_asked_for(*argv), "the run never reached Immich"

        # Everything a runner logs, and everything `ps` shows, is in argv.
        assert TITLE not in " ".join(argv)
        preset = create_preset(MemoryType.SPECIAL_DAY, **resolve_special_day(DAY, "special_day"))
        assert preset.name == TITLE

    def test_a_typed_title_still_outranks_the_catalogues(self, catalogue_at) -> None:
        catalogue_at(ENTRY)
        special_day = resolve_special_day(DAY, "special_day")

        assert name_from_catalogue(special_day, "Our own name for it", None) == (
            "Our own name for it",
            ENTRY["subtitle"],
        )
        assert name_from_catalogue(special_day, None, None) == (TITLE, ENTRY["subtitle"])


class TestHowLongTheDayLasted:
    """Length comes from the day's own evidence, and the evidence has a trap."""

    def test_a_run_that_outlasted_the_calendar_day_is_not_read_as_24_hours(
        self, catalogue_at
    ) -> None:
        # active_hours counts distinct hours touched, so it saturates at 24 --
        # which a 45-hour run reaches before lunch on its second day. The
        # recorded extent is the only reading that survives midnight.
        catalogue_at(
            {
                **ENTRY,
                "active_hours": 24,
                "run_start": "2016-06-12T09:00:00+02:00",
                "run_end": "2016-06-14T06:00:00+02:00",
            }
        )

        special_day = resolve_special_day(DAY, "special_day")

        assert special_day["active_hours"] == pytest.approx(45.0)

    def test_a_day_with_no_recorded_extent_falls_back_to_the_hours_it_counted(
        self, catalogue_at
    ) -> None:
        catalogue_at(ENTRY)

        special_day = resolve_special_day(DAY, "special_day")

        assert special_day["active_hours"] == pytest.approx(9.0)
