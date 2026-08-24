"""A day the library says something happened on, as a memory type.

Fixture days are invented. The real catalogue names real people and places, and
none of that belongs in a test file.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from immich_memories.cli._date_resolution import (
    default_duration_for_type,
    duration_from_date_range,
)
from immich_memories.memory_types.date_builders import build_special_day
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType


class TestBuildSpecialDay:
    """The window is the scope, and a missing window is the calendar day."""

    def test_no_window_covers_the_whole_calendar_day(self) -> None:
        day = date(2016, 6, 12)

        scope = build_special_day(day, None)

        assert scope.start == datetime(2016, 6, 12, 0, 0, 0)
        assert scope.end == datetime(2016, 6, 12, 23, 59, 59)

    def test_a_window_is_the_scope_exactly_with_its_offset_intact(self) -> None:
        # Immich returns file_created_at tz-aware, and the offset has to survive
        # to the takenAfter/takenBefore query or the memory scopes to the wrong
        # hours of the day.
        brussels = timezone(timedelta(hours=2))
        window = (
            datetime(2016, 6, 12, 13, 45, 0, tzinfo=brussels),
            datetime(2016, 6, 12, 16, 3, 0, tzinfo=brussels),
        )

        scope = build_special_day(date(2016, 6, 12), window)

        assert (scope.start, scope.end) == window
        assert scope.start.utcoffset() == timedelta(hours=2)

    def test_a_window_running_past_midnight_crosses_the_date_boundary(self) -> None:
        # A long evening out ends on the next date. Clamping the window back to
        # the day it started on would drop everything after midnight.
        window = (datetime(2019, 12, 31, 21, 0, 0), datetime(2020, 1, 1, 2, 30, 0))

        scope = build_special_day(date(2019, 12, 31), window)

        assert scope.end.date() == date(2020, 1, 1)
        assert (scope.start, scope.end) == window


class TestSpecialDayPreset:
    """The catalogue named the day; the preset carries that name, or refuses."""

    def test_the_catalogue_title_names_the_memory(self) -> None:
        preset = create_preset(
            MemoryType.SPECIAL_DAY,
            day=date(2016, 6, 12),
            title="A long evening out",
            subtitle="Somebody's leap day",
        )

        assert preset.name == "A long evening out"
        assert preset.memory_type is MemoryType.SPECIAL_DAY

    def test_an_empty_title_falls_back_to_what_the_day_was(self) -> None:
        preset = create_preset(
            MemoryType.SPECIAL_DAY,
            day=date(2016, 6, 12),
            title="   ",
            what="A very long walk",
        )

        assert preset.name == "A very long walk"

    def test_a_day_with_no_name_at_all_is_refused(self) -> None:
        # Refuse over fake: a generic "Memories from 12 June 2016" card would
        # claim the library found something it could not describe.
        with pytest.raises(ValueError, match="nothing truthful"):
            create_preset(MemoryType.SPECIAL_DAY, day=date(2016, 6, 12))


class TestSpecialDayDuration:
    """A day that stayed awake longer earns a longer memory, within bounds."""

    def test_a_short_window_runs_shorter_than_a_whole_long_day(self) -> None:
        entry = {"day": date(2016, 6, 12), "title": "An afternoon at the track"}
        trimmed = create_preset(
            MemoryType.SPECIAL_DAY,
            window=(datetime(2016, 6, 12, 13, 0), datetime(2016, 6, 12, 15, 18)),
            **entry,
        )
        all_day = create_preset(MemoryType.SPECIAL_DAY, active_hours=18.0, **entry)

        assert trimmed.default_duration_seconds < all_day.default_duration_seconds
        for preset in (trimmed, all_day):
            assert 60 <= preset.default_duration_seconds <= 180

    def test_the_cli_reads_the_preset_rather_than_the_month_curve(self) -> None:
        # duration_from_date_range is a quadratic fitted on 1-12 months; at a
        # one-day span it evaluates negative and clamps to the 30s floor, which
        # is exactly the shape #511 was about. A 289-photo day is not 30
        # seconds long.
        day = date(2016, 6, 12)
        scope = build_special_day(day, None)

        duration = default_duration_for_type(
            "special_day",
            scope,
            preset_params={"day": day, "title": "A day out", "active_hours": 18.0},
        )

        assert duration == 138.0
        assert duration_from_date_range(scope) == 30.0
