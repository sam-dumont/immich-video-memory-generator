"""A multi-window memory has to say what each window contributed.

The fetch loop queried every range, concatenated, deduped and printed one
combined total. A then-and-now whose older half returned nothing rendered as
a memory of the recent half alone, and the run looked clean.
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from immich_memories.cli._asset_fetch import fetch_videos
from immich_memories.cli._helpers import set_quiet_mode
from immich_memories.timeperiod import DateRange

NOW = DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 12, 31, 23, 59, 59))
THEN = DateRange(start=datetime(2016, 1, 1), end=datetime(2016, 12, 31, 23, 59, 59))


def _asset(asset_id: str, when: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=asset_id, file_created_at=when)


class _Immich:
    """WHY: Immich is the external boundary. Returns whatever each window holds."""

    def __init__(self, by_range: dict[DateRange, list]) -> None:
        self._by_range = by_range

    def get_videos_for_date_range(self, date_range: DateRange) -> list:
        return self._by_range.get(date_range, [])

    def get_videos_for_person_and_date_range(self, person_id: str, date_range: DateRange) -> list:
        """Immich filters server-side, so a window can come back empty for one
        person while holding plenty for everyone else."""
        return [
            a
            for a in self._by_range.get(date_range, [])
            if any(p.id == person_id for p in getattr(a, "people", None) or [])
        ]


class _Progress:
    """WHY: the Rich live display needs a terminal; the fetch only calls these two."""

    def add_task(self, *_args, **_kwargs) -> int:
        return 0

    def update(self, *_args, **_kwargs) -> None:
        return None


@pytest.fixture(autouse=True)
def _quiet():
    """Route the print helpers through logging so caplog can see them."""
    set_quiet_mode(True)
    yield
    set_quiet_mode(False)


def _fetch(by_range, caplog):
    with caplog.at_level(logging.INFO):
        assets = fetch_videos(
            client=_Immich(by_range),
            progress=_Progress(),
            date_ranges=[NOW, THEN],
            person_ids=[],
        )
    return assets, caplog.text


def test_each_window_reports_what_it_contributed(caplog) -> None:
    by_range = {
        NOW: [_asset("a", datetime(2026, 3, 1)), _asset("b", datetime(2026, 4, 1))],
        THEN: [_asset("c", datetime(2016, 5, 1))],
    }

    assets, output = _fetch(by_range, caplog)

    assert len(assets) == 3
    assert "2 " in output and "1 " in output
    assert "2026" in output and "2016" in output


def test_a_window_that_returned_nothing_is_a_warning(caplog) -> None:
    """The half that makes it a then-and-now came back empty."""
    by_range = {NOW: [_asset("a", datetime(2026, 3, 1))], THEN: []}

    _, output = _fetch(by_range, caplog)

    assert "2016" in output
    assert "no " in output.lower() or "empty" in output.lower()


def test_a_single_window_memory_says_nothing_extra(caplog) -> None:
    """Every other memory type has one range; the breakdown would be noise."""
    with caplog.at_level(logging.INFO):
        fetch_videos(
            client=_Immich({NOW: [_asset("a", datetime(2026, 3, 1))]}),
            progress=_Progress(),
            date_ranges=[NOW],
            person_ids=[],
        )

    assert "2026:" not in caplog.text


def _person(person_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=person_id, name=name)


def _asset_with(asset_id: str, when: datetime, people: list) -> SimpleNamespace:
    return SimpleNamespace(id=asset_id, file_created_at=when, people=people)


ALEX = _person("p-alex", "Alex")


def _fetch_for_person(by_range, caplog, person_ids):
    with caplog.at_level(logging.INFO):
        fetch_videos(
            client=_Immich(by_range),
            progress=_Progress(),
            date_ranges=[NOW, THEN],
            person_ids=person_ids,
        )
    return caplog.text


def test_a_person_missing_from_an_era_is_called_out_by_name(caplog) -> None:
    """A then-and-now anchored on someone absent from "then" is just a now.

    Neither window is empty, so the per-window counts look healthy. Only the
    person's own spread shows the memory cannot do what its title claims.
    """
    by_range = {
        NOW: [_asset_with("a", datetime(2026, 3, 1), [ALEX])],
        THEN: [_asset_with("b", datetime(2016, 5, 1), [_person("p-sam", "Sam")])],
    }

    output = _fetch_for_person(by_range, caplog, ["p-alex"])

    assert "Alex" in output
    assert "2016" in output


def test_a_person_present_on_both_sides_is_not_warned_about(caplog) -> None:
    by_range = {
        NOW: [_asset_with("a", datetime(2026, 3, 1), [ALEX])],
        THEN: [_asset_with("b", datetime(2016, 5, 1), [ALEX])],
    }

    output = _fetch_for_person(by_range, caplog, ["p-alex"])

    assert "does not appear" not in output


def test_a_single_window_memory_never_checks_a_persons_spread(caplog) -> None:
    """Only a multi-window memory can be lopsided across windows."""
    with caplog.at_level(logging.INFO):
        fetch_videos(
            client=_Immich({NOW: [_asset_with("a", datetime(2026, 3, 1), [ALEX])]}),
            progress=_Progress(),
            date_ranges=[NOW],
            person_ids=["p-alex"],
        )

    assert "Alex" not in caplog.text
