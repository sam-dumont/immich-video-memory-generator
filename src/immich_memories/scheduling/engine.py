"""Scheduler engine — cron evaluation, job queue, and execution loop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

logger = logging.getLogger(__name__)


@dataclass
class PendingJob:
    """A scheduled job with its calculated fire time."""

    schedule: ScheduleEntry
    fire_time: datetime


class Scheduler:
    """Evaluates cron schedules and determines what to run next."""

    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config

    def _zone(self) -> tzinfo:
        """The zone the cron expressions are written in.

        The config validates the name, so reaching the fallback means the box
        has no tz database rather than that the user mistyped it.
        """
        try:
            return ZoneInfo(self.config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                f"Timezone '{self.config.timezone}' is unknown on this system; "
                f"evaluating cron in UTC"
            )
            return UTC

    def get_next_jobs(self, now: datetime | None = None) -> list[PendingJob]:
        """Calculate next fire time for each enabled schedule, sorted earliest first."""
        if now is None:
            now = datetime.now(tz=UTC)

        # WHY: a cron expression is written in wall-clock terms -- "9am" means
        # 9am where the library lives. Evaluated in UTC, every schedule outside
        # UTC fired at the wrong hour, and a new year's or month's job could
        # resolve the wrong period. The fire time stays in that zone so the
        # params are resolved from the calendar date the user meant.
        zone = self._zone()
        local_now = now.astimezone(zone)

        jobs: list[PendingJob] = []
        for entry in self.config.schedules:
            if not entry.enabled:
                continue

            cron = croniter(entry.cron, local_now)
            fire_time = cron.get_next(datetime)
            if fire_time.tzinfo is None:
                fire_time = fire_time.replace(tzinfo=zone)

            jobs.append(PendingJob(schedule=entry, fire_time=fire_time))

        jobs.sort(key=lambda j: j.fire_time)
        return jobs

    def seconds_until_next(self, now: datetime | None = None) -> float | None:
        """Seconds until the next job should fire. None if no jobs."""
        if now is None:
            now = datetime.now(tz=UTC)

        jobs = self.get_next_jobs(now)
        if not jobs:
            return None

        delta = (jobs[0].fire_time - now).total_seconds()
        return max(0.0, delta)
