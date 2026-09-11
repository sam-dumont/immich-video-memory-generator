"""Every scheduled run must reach `generate` meaning what the schedule said.

These tests parse the daemon's argv with the real Click command rather than
asserting on the list it built, because the three defects this file was written
for -- a flag that does not exist, a missing flag, and minutes handed to a
seconds option -- all survive a list-shaped assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from immich_memories.memory_types.registry import OFFERED_MEMORY_TYPES
from immich_memories.scheduling.daemon import (
    UnschedulableJob,
    _generate_command,
    execute_job,
)
from immich_memories.scheduling.engine import PendingJob
from immich_memories.scheduling.executor import resolve_schedule_params
from immich_memories.scheduling.models import ScheduleEntry
from tests.cli_argv_contract import memory_scope, parse_generate_argv

FIRE_TIME = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


def _scheduled_params(**entry_kwargs) -> dict:
    entry = ScheduleEntry(cron="0 9 * * *", **entry_kwargs)
    return resolve_schedule_params(entry, FIRE_TIME)


def _parse_schedule(**entry_kwargs) -> dict:
    return parse_generate_argv(_generate_command(_scheduled_params(**entry_kwargs), None))


class TestScheduledArgvParses:
    """A schedule that cannot be parsed is a schedule that never runs."""

    @pytest.mark.parametrize(
        "memory_type",
        ["year_in_review", "monthly_highlights", "on_this_day", "trip"],
    )
    def test_every_auto_resolved_memory_type_parses(self, memory_type: str) -> None:
        params = _parse_schedule(name="scheduled", memory_type=memory_type)
        assert params["memory_type"] == memory_type

    def test_on_this_day_carries_no_unknown_date_flag(self) -> None:
        """The defect: --target-date was sent and `generate` has no such option."""
        argv = _generate_command(_scheduled_params(name="daily", memory_type="on_this_day"), None)
        assert "--target-date" not in argv
        assert parse_generate_argv(argv)["memory_type"] == "on_this_day"

    def test_previous_year_reaches_the_year_option(self) -> None:
        params = _parse_schedule(name="yearly", memory_type="year_in_review")
        assert params["year"] == FIRE_TIME.year - 1

    def test_previous_month_reaches_the_month_option(self) -> None:
        params = _parse_schedule(name="monthly", memory_type="monthly_highlights")
        assert (params["year"], params["month"]) == (2026, 6)


class TestScheduledTripSelectsSomething:
    """A trip run with no selector renders nothing and still exits zero."""

    def test_trip_asks_for_every_detected_trip(self) -> None:
        params = _parse_schedule(name="trips", memory_type="trip")
        assert params["all_trips"] is True
        assert params["year"] == FIRE_TIME.year - 1

    def test_an_explicit_trip_selector_is_not_overruled(self) -> None:
        params = _parse_schedule(name="trips", memory_type="trip", params={"trip_index": 2})
        assert params["trip_index"] == 2
        assert params["all_trips"] is False

    def test_a_month_selector_is_not_overruled(self) -> None:
        params = _parse_schedule(name="trips", memory_type="trip", params={"month": 7})
        assert params["month"] == 7
        assert params["all_trips"] is False


class TestExplicitScopeSurvives:
    """A scope the user wrote in the schedule has to reach the command."""

    def test_season_reaches_the_season_option(self) -> None:
        params = _parse_schedule(
            name="summer", memory_type="season", params={"season": "summer", "year": 2024}
        )
        assert params["season"] == "summer"
        assert params["year"] == 2024

    def test_a_param_no_option_expresses_is_refused(self) -> None:
        with pytest.raises(UnschedulableJob, match="target_date"):
            _generate_command(
                _scheduled_params(
                    name="daily", memory_type="on_this_day", params={"target_date": "2026-07-15"}
                ),
                None,
            )

    def test_a_refused_param_fails_the_job_without_stopping_the_daemon(self) -> None:
        entry = ScheduleEntry(
            name="daily",
            memory_type="on_this_day",
            cron="0 9 * * *",
            params={"target_date": "2026-07-15"},
        )
        job = PendingJob(schedule=entry, fire_time=FIRE_TIME)

        # WHY: run_bounded_process spawns the real CLI as a subprocess.
        # WHY: _notify_if_configured sends a real webhook for every job.
        with (
            patch("immich_memories.scheduling.daemon.run_bounded_process") as spawn,
            patch("immich_memories.scheduling.daemon._notify_if_configured") as notify,
        ):
            execute_job(job)

        spawn.assert_not_called()
        assert notify.call_args.kwargs["success"] is False


class TestScheduledDurationUnit:
    """duration_minutes is minutes; --duration is seconds."""

    def test_minutes_become_seconds(self) -> None:
        params = _parse_schedule(
            name="monthly", memory_type="monthly_highlights", duration_minutes=3
        )
        assert params["duration"] == 180

    def test_no_duration_leaves_the_preset_default(self) -> None:
        params = _parse_schedule(name="monthly", memory_type="monthly_highlights")
        assert params["duration"] is None


# What a user has to write in the schedule for each memory type to mean
# anything: the types that are not listed need nothing beyond their name.
SCHEDULE_FOR_TYPE = {
    "season": {"params": {"season": "summer"}},
    "person_spotlight": {"person_names": ["Someone"]},
    "multi_person": {"person_names": ["Someone", "Another"]},
    "holiday": {"params": {"holiday": "christmas"}},
    "album": {"params": {"from_album": "Some Album"}},
}

# special_day takes its window from the catalogue rather than from flags, so a
# cron expression cannot say which day it means.
UNSCHEDULABLE_TYPES = {"special_day"}


class TestEveryOfferedMemoryType:
    """A schedule names a memory type; all ten have to mean something."""

    @pytest.mark.parametrize("memory_type", [t.value for t in OFFERED_MEMORY_TYPES])
    def test_a_schedule_either_resolves_a_memory_or_is_refused(self, memory_type: str) -> None:
        params = _scheduled_params(
            name="scheduled", memory_type=memory_type, **SCHEDULE_FOR_TYPE.get(memory_type, {})
        )

        if memory_type in UNSCHEDULABLE_TYPES:
            with pytest.raises(UnschedulableJob, match=memory_type):
                _generate_command(params, None)
            return

        parsed = parse_generate_argv(_generate_command(params, None))
        window, ranges = memory_scope(parsed)

        assert parsed["memory_type"] == memory_type
        assert ranges or parsed["from_album"], f"{memory_type} resolved no range to search"
        assert window is not None

    def test_holiday_reaches_the_holiday_option(self) -> None:
        params = _parse_schedule(
            name="christmas", memory_type="holiday", params={"holiday": "christmas"}
        )
        assert params["holiday"] == "christmas"

    def test_an_album_schedule_carries_the_album_and_no_date(self) -> None:
        """--from-album is the whole scope; generate refuses date flags beside it."""
        params = _parse_schedule(
            name="album", memory_type="album", params={"from_album": "Some Album"}
        )
        assert params["from_album"] == "Some Album"
        assert params["year"] is None
        assert params["month"] is None
