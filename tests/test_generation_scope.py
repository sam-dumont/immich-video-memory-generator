"""Resolving what a memory covers: date range(s), or an album (#270)."""

from __future__ import annotations

from datetime import datetime

import click
import pytest
from rich.table import Table

from immich_memories.cli._generate_display import _add_scope_rows, _format_target_duration
from immich_memories.cli.generate import (
    _reject_album_scope_conflicts,
    _resolve_generation_scope,
)
from immich_memories.timeperiod import DateRange


def _scope(**overrides):
    kwargs = {
        "from_album": None,
        "year": None,
        "start": None,
        "end": None,
        "period": None,
        "birthday": None,
        "memory_type": None,
        "season": None,
        "month": None,
        "hemisphere": "north",
        "years_back": None,
        "on_this_day_target": None,
    }
    kwargs.update(overrides)
    return _resolve_generation_scope(**kwargs)


def test_album_mode_searches_no_date_ranges_at_all():
    """The album is the pool, so nothing is discovered by date."""
    _display_range, ranges = _scope(from_album="Trip 2025")

    assert ranges == []


def test_a_year_resolves_to_one_searchable_range():
    display, ranges = _scope(year=2025)

    assert len(ranges) == 1
    assert display.start.year == 2025


def test_on_this_day_spans_its_ranges_for_display_but_searches_each():
    display, ranges = _scope(memory_type="on_this_day", years_back=3)

    assert len(ranges) > 1
    assert display.start < display.end


def test_album_mode_rejects_flags_that_would_scope_it_differently():
    with pytest.raises(click.UsageError) as exc:
        _reject_album_scope_conflicts(
            year=2025,
            start=None,
            end=None,
            period=None,
            birthday=None,
            season=None,
            month=None,
            memory_type=None,
            person_names=["Alice"],
        )

    assert "--year" in str(exc.value)
    assert "--person" in str(exc.value)


def test_album_mode_accepts_a_bare_album():
    _reject_album_scope_conflicts(
        year=None,
        start=None,
        end=None,
        period=None,
        birthday=None,
        season=None,
        month=None,
        memory_type=None,
        person_names=[],
    )


def test_the_parameters_table_names_the_album_instead_of_a_time_period():
    table = Table()
    table.add_column("Setting")
    table.add_column("Value")

    _add_scope_rows(
        table,
        album_ref="Trip 2025",
        date_range=DateRange(start=datetime(2025, 1, 1), end=datetime(2025, 12, 31)),
    )

    assert [c._cells for c in table.columns] == [["Album"], ["Trip 2025"]]


def test_the_parameters_table_falls_back_to_the_time_period():
    table = Table()
    table.add_column("Setting")
    table.add_column("Value")

    _add_scope_rows(
        table,
        album_ref=None,
        date_range=DateRange(start=datetime(2025, 1, 1), end=datetime(2025, 12, 31)),
    )

    assert table.columns[0]._cells == ["Time Period", "Duration"]


def test_an_unknown_target_duration_reads_as_auto():
    assert _format_target_duration(None) == "auto"
    assert _format_target_duration(45.0) == "45s"
    assert _format_target_duration(120.0) == "2.0 min"
