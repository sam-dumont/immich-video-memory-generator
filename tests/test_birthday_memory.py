"""A birthday memory runs birthday to birthday, whatever month it is today.

The windows are event-anchored: the rolling year ends on the birthday being
celebrated, and the flashbacks sit on that birthday in earlier years. Nothing
here is allowed to depend on which calendar year the request happened to name.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from immich_memories.automation.calendar_detectors import BirthdayDetector
from immich_memories.automation.generation_request import GenerationRequest
from immich_memories.cli._asset_fetch import fetch_videos
from immich_memories.cli._date_resolution import default_duration_for_type, resolve_date_range
from immich_memories.cli._helpers import set_active_display
from immich_memories.memory_types.date_builders import (
    BIRTHDAY_HISTORY_FROM,
    birthday_anchor,
    build_birthday_windows,
)
from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange


class TestRollingYear:
    """The recent window is the year that ends on the birthday."""

    def test_february_birthday_runs_february_to_february(self):
        """The bug that named the issue: a 7 February birthday, a July window.

        The window used to be picked by a calendar year number and run forward
        from it, so the birthday the memory is named for fell outside it.
        """
        windows = build_birthday_windows(date(2018, 2, 7), year=2026)

        assert windows[0].start == datetime(2025, 2, 8)
        assert windows[0].end == datetime(2026, 2, 7, 23, 59, 59)

    def test_the_birthday_being_celebrated_is_inside_the_memory(self):
        windows = build_birthday_windows(date(2018, 2, 7), year=2026)

        assert windows[0].contains(datetime(2026, 2, 7, 12, 0))

    def test_the_previous_birthday_belongs_to_the_previous_memory(self):
        """The rolling year starts the day after, so last year's party is history."""
        windows = build_birthday_windows(date(2018, 2, 7), year=2026)

        assert not windows[0].contains(datetime(2025, 2, 7, 12, 0))

    def test_no_year_celebrates_the_most_recent_birthday(self):
        """A run in August is still about February's birthday, not next year's."""
        windows = build_birthday_windows(date(2018, 2, 7), today=date(2026, 8, 25))

        assert windows[0].end == datetime(2026, 2, 7, 23, 59, 59)

    def test_a_run_on_the_birthday_itself_celebrates_it(self):
        windows = build_birthday_windows(date(2018, 2, 7), today=date(2026, 2, 7))

        assert windows[0].end == datetime(2026, 2, 7, 23, 59, 59)

    def test_the_cli_does_not_demand_a_year_for_a_birthday_memory(self):
        """Every other spotlight needs --year; this one knows which birthday."""
        windows = resolve_date_range(
            year=None,
            start=None,
            end=None,
            period=None,
            birthday="02-07",
            memory_type="person_spotlight",
        )

        assert isinstance(windows, list)
        assert windows[0].end.month == 2
        assert windows[0].end.day == 7


class TestHistory:
    """The flashbacks sit on the birthday in each earlier year."""

    def test_each_earlier_birthday_gets_its_own_window(self):
        windows = build_birthday_windows(date(2018, 2, 7), year=2026, years_back=3)

        assert [w.start.year for w in windows[BIRTHDAY_HISTORY_FROM:]] == [2025, 2024, 2023]

    def test_a_flashback_is_the_birthday_plus_or_minus_a_day(self):
        windows = build_birthday_windows(date(2018, 2, 7), year=2026, years_back=1)

        assert windows[BIRTHDAY_HISTORY_FROM].start == datetime(2025, 2, 6)
        assert windows[BIRTHDAY_HISTORY_FROM].end == datetime(2025, 2, 8, 23, 59, 59)

    def test_the_default_reach_matches_on_this_day(self):
        """One convention for how far back a memory that spans years looks."""
        on_this_day = create_preset(MemoryType.ON_THIS_DAY, target_date=date(2026, 2, 7))
        windows = build_birthday_windows(date(1990, 2, 7), year=2026)

        assert len(windows) - BIRTHDAY_HISTORY_FROM == len(on_this_day.date_ranges)

    def test_the_display_span_reaches_from_the_oldest_flashback_to_the_birthday(self):
        windows = build_birthday_windows(date(2018, 2, 7), year=2026, years_back=4)

        assert windows[-1].start == datetime(2022, 2, 6)
        assert windows[0].end == datetime(2026, 2, 7, 23, 59, 59)

    def test_no_history_asked_for_leaves_the_rolling_year_alone(self):
        assert len(build_birthday_windows(date(2018, 2, 7), year=2026, years_back=0)) == 1


class TestWhereTheBirthdayComesFrom:
    """Immich is the source of truth; a typed date is an override for one run."""

    def test_the_immich_birth_date_anchors_the_memory(self):
        assert birthday_anchor(datetime(2018, 2, 7, 0, 0), None, person_name="Alice") == date(
            2018, 2, 7
        )

    def test_a_typed_date_overrides_what_immich_holds(self):
        anchor = birthday_anchor(datetime(2018, 2, 7), date(2000, 11, 20), person_name="Alice")

        assert anchor == date(2000, 11, 20)

    def test_a_typed_date_still_answers_when_immich_holds_nothing(self):
        assert birthday_anchor(None, date(2000, 11, 20), person_name="Alice") == date(2000, 11, 20)

    def test_neither_is_an_error_that_says_where_to_set_it(self):
        """Refuse over fake: the error is how the user learns Immich drives this."""
        with pytest.raises(ValueError, match="People") as caught:
            birthday_anchor(None, None, person_name="Alice")

        assert "Alice" in str(caught.value)
        assert "birth date" in str(caught.value)


class _EmptyLibrary:
    """Stands in for the Immich HTTP API, which has nothing to offer.

    WHY: the only boundary the fetch crosses, and an empty library is what
    makes every history window report zero — which is the case under test.
    """

    def get_videos_for_person_and_date_range(self, _person_id, _date_range) -> list:
        return []

    def get_videos_for_date_range(self, _date_range) -> list:
        return []


class _RecordingDisplay:
    """The terminal the CLI's print helpers write to, keeping what they said.

    WHY: set_active_display is the seam the helpers already route through, and
    it does not depend on which stdout the module-level Rich console bound to
    when it was imported — which is what made reading this back through capsys
    depend on test ordering.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print_message(self, message: str) -> None:
        self.lines.append(message)

    def add_task(self, _description: str, **_fields) -> int:
        return 0

    def update(self, _task_id: int, **_kwargs) -> None:
        return None


class TestSparseHistoryReporting:
    """Five empty flashbacks are a library; an empty rolling year is a bug."""

    def _report(self, *, history_from: int | None) -> str:
        display = _RecordingDisplay()
        set_active_display(display)
        try:
            fetch_videos(
                client=_EmptyLibrary(),
                progress=display,
                date_ranges=build_birthday_windows(date(2018, 2, 7), year=2026),
                person_ids=["person-alice"],
                history_from=history_from,
            )
        finally:
            set_active_display(None)
        return "\n".join(display.lines)

    def test_the_flashbacks_are_summarised_rather_than_warned_about(self):
        out = self._report(history_from=BIRTHDAY_HISTORY_FROM)

        assert "history: 0 of 5 earlier windows hold material" in out

    def test_an_empty_rolling_year_still_gets_its_own_warning(self):
        out = self._report(history_from=BIRTHDAY_HISTORY_FROM)

        assert "2025-02-08..2026-02-07" in out

    def test_without_the_mark_every_window_shouts(self):
        """The behaviour the mark exists to avoid, pinned so it stays avoided."""
        out = self._report(history_from=None)

        assert out.count("no videos found for") == 6


class TestAutomationRoundTrip:
    """What the nightly runner proposes is what the CLI it spawns renders."""

    def test_the_candidate_window_survives_the_argv(self):
        """--year names the celebrated birthday, so it must be read off the end.

        The detector picks a completed birthday year and hands the child
        process a year number; taking that from the start of the window would
        render the year before the one the runner proposed, silently.
        """
        person = SimpleNamespace(id="p1", name="Alice", birth_date=date(2000, 2, 7))

        # WHY: BirthdayDetector reads no config field on this path; the object
        # is only there to satisfy the signature.
        candidate = BirthdayDetector().detect(
            {}, [person], set(), MagicMock(), date(2026, 2, 20), person_asset_counts={"p1": 40}
        )[0]
        argv = GenerationRequest.from_candidate(candidate, upload=False).to_argv()
        year = int(argv[argv.index("--year") + 1])

        rolling = build_birthday_windows(person.birth_date, year)[0]

        assert rolling.start.date() == candidate.date_range_start
        assert rolling.end.date() == candidate.date_range_end


class TestLength:
    """The memory is a year long, whatever the flashbacks reach back over."""

    def test_the_flashbacks_do_not_shrink_it_to_the_floor(self):
        """The span curve goes negative past ~40 months and clamps to 30s (#511).

        Collapsed for display, a birthday memory spans decades; what it is
        actually made of is the rolling year.
        """
        windows = build_birthday_windows(date(1990, 2, 7), year=2026)
        display = DateRange(start=windows[-1].start, end=windows[0].end)

        length = default_duration_for_type("person_spotlight", display, primary_window=windows[0])

        assert length == pytest.approx(600.0)


class TestLeapDay:
    """A 29 February birthday is celebrated on the 28th in an ordinary year."""

    def test_the_rolling_year_ends_on_the_twenty_eighth(self):
        windows = build_birthday_windows(date(2000, 2, 29), year=2026)

        assert windows[0].end == datetime(2026, 2, 28, 23, 59, 59)

    def test_a_leap_year_flashback_still_reaches_the_twenty_ninth(self):
        """±1 day around the 28th covers the 29th in the years that have one."""
        windows = build_birthday_windows(date(2000, 2, 29), year=2026, years_back=2)

        assert windows[-1].contains(datetime(2024, 2, 29, 12, 0))
