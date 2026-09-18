"""A day the scan never catalogued can still be filmed, and is still never invented.

The owner named a race day the scan had not found. `--day` refused it in about
a second, so a day he knew was special could not be filmed at all without
first running a scan over that year. The refusal was there to stop a "Memories
from 12 June 2016" card going out over a day nothing could name.

Both hold now, because naming is a ladder rather than a gate: an explicit
--title, then the catalogue's title when the day has a row, then the model
writing an occasion title from the day's own facts, then the date. Nothing on
that ladder names an event the facts do not name.

Every day here is invented; the real catalogue names real people and places.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.cli.generate_resolution import name_from_catalogue, resolve_special_day
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType

_DAY = date(2021, 4, 4)
_CATALOGUED = date(2016, 6, 12)
_ROW = {
    "day": "2016-06-12",
    "title": "An afternoon at the track",
    "subtitle": "Somebody's first race",
    "what": "A long day out",
    "photos": 210,
    "active_hours": 9,
}


@pytest.fixture
def catalogue(tmp_path: Path):
    """A catalogue file holding one day, in place of the owner's own."""
    path = tmp_path / "special-days.json"
    path.write_text(json.dumps([_ROW]))
    # WHY: the catalogue lives in the real home directory, which a unit test must not read.
    with patch("immich_memories.automation.catalogue.default_catalogue_path", lambda: path):
        yield path


def test_a_day_the_catalogue_never_found_still_resolves(catalogue) -> None:
    """The refusal is gone; what comes back is the day and nothing invented."""
    params = resolve_special_day(_DAY, "special_day")

    assert params is not None
    assert params["day"] == _DAY
    assert params["window"] is None, "with no row there is no occasion inside the day"
    assert not params["title"]
    assert json.loads(catalogue.read_text()) == [_ROW], "filming a day catalogues nothing"


def test_an_uncatalogued_day_is_named_by_its_date_when_nothing_else_names_it(catalogue) -> None:
    """The last rung of the ladder: a date is not an invented event."""
    preset = create_preset(MemoryType.SPECIAL_DAY, **resolve_special_day(_DAY, "special_day"))

    assert preset.name == "4 April 2021"


def test_the_catalogue_still_names_the_day_it_has_a_row_for(catalogue) -> None:
    params = resolve_special_day(_CATALOGUED, "special_day")

    assert name_from_catalogue(params, None, None) == (_ROW["title"], _ROW["subtitle"])


def test_an_explicit_title_still_wins_over_both(catalogue) -> None:
    catalogued = resolve_special_day(_CATALOGUED, "special_day")
    uncatalogued = resolve_special_day(_DAY, "special_day")

    assert name_from_catalogue(catalogued, "Our own name", None)[0] == "Our own name"
    assert name_from_catalogue(uncatalogued, "Our own name", None)[0] == "Our own name"


def test_an_uncatalogued_day_leaves_the_naming_to_the_model(catalogue) -> None:
    """No title from the file means the reader is asked, as for any occasion memory."""
    from immich_memories.titles.llm_titles import OCCASION_MEMORY_TYPES, memory_title_facts

    params = resolve_special_day(_DAY, "special_day")
    title, _ = name_from_catalogue(params, None, None)

    assert title is None, "nothing may stand in for the model here"
    assert "special_day" in OCCASION_MEMORY_TYPES
    assert memory_title_facts(params).occasion_name is None, "no row, no occasion to claim"


def test_a_catalogued_day_with_nothing_written_on_it_is_not_refused(tmp_path) -> None:
    """A row the scan could not name is a gap in the file, not a reason to stop.

    It used to error with "nothing truthful to call the memory", which is the
    right instinct and the wrong remedy: the day is still the day.
    """
    path = tmp_path / "special-days.json"
    path.write_text(json.dumps([{"day": "2016-06-12", "title": "  ", "what": "", "photos": 210}]))
    # WHY: the catalogue lives in the real home directory, which a unit test must not read.
    with patch("immich_memories.automation.catalogue.default_catalogue_path", lambda: path):
        params = resolve_special_day(_CATALOGUED, "special_day")

    assert not params["title"]
    assert create_preset(MemoryType.SPECIAL_DAY, **params).name == "12 June 2016"


_UNNAMED = date(2016, 8, 13)
_UNNAMED_ROW = {
    "day": "2016-08-13",
    "title": "",
    "subtitle": "",
    "what": "An outdoor concert in a field",
    "photos": 180,
    "active_hours": 11,
}


@pytest.fixture
def unnamed_catalogue(tmp_path: Path):
    """A row the scan described but never named."""
    path = tmp_path / "special-days.json"
    path.write_text(json.dumps([_UNNAMED_ROW]))
    # WHY: the catalogue lives in the real home directory, which a unit test must not read.
    with patch("immich_memories.automation.catalogue.default_catalogue_path", lambda: path):
        yield path


def test_a_row_the_scan_described_but_never_named_hands_its_words_over_as_a_fact(
    unnamed_catalogue,
) -> None:
    """A description is a fact about the day, not the title of a film about it.

    Handing it over as the title pinned the memory before anything else on the
    ladder ran, so the reader was never asked and the album the pictures sit in
    was never looked up.
    """
    params = resolve_special_day(_UNNAMED, "special_day")

    assert params is not None
    assert not params["title"]
    assert params["what"] == "An outdoor concert in a field"
