"""Daily automation timer that runs inside the UI process.

Docker users have no host cron and the container's only process is the UI, so
`automation.enabled: true` makes that process fire the same `auto run` decision the
CLI does — same lease, history, and delivery retry — once a day at `automation.daily_at`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from immich_memories.automation.models import AutoRunResult
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

# How often the loop re-reads config; also the worst-case lateness of a fire.
POLL_SECONDS = 30.0


@dataclass(frozen=True)
class SchedulerSnapshot:
    """What `/health` and the settings page can say about the in-process timer."""

    enabled: bool
    daily_at: str | None
    next_run: datetime | None
    running: bool
    last_fired_at: datetime | None
    last_outcome: str | None
    last_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "daily_at": self.daily_at,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "running": self.running,
            "last_fired_at": self.last_fired_at.isoformat() if self.last_fired_at else None,
            "last_outcome": self.last_outcome,
            "last_reason": self.last_reason,
        }


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _slot_today(now: datetime, daily_at: str) -> datetime:
    at = time.fromisoformat(daily_at)
    return now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)


class InProcessScheduler:
    """One asyncio loop: read config, fire the daily decision once per calendar day.

    Fires at (or as soon as possible after) `daily_at`; a container that was down at that
    time catches up on start, like systemd's `Persistent=true`. Uses `run_once` in a worker
    thread because `AutoRunner.run_one` blocks for the whole generation.
    """

    def __init__(
        self,
        config_provider: Callable[[], Config],
        *,
        run_once: Callable[[Config], AutoRunResult] | None = None,
        clock: Callable[[], datetime] = _local_now,
    ) -> None:
        self._config_provider = config_provider
        self._run_once = run_once or _run_auto_once
        self._clock = clock
        self._enabled = False
        self._daily_at: str | None = None
        self._next_run: datetime | None = None
        self._running = False
        self._last_fired_at: datetime | None = None
        self._last_fired_date: date | None = None
        self._last_outcome: str | None = None
        self._last_reason: str | None = None

    def snapshot(self) -> SchedulerSnapshot:
        return SchedulerSnapshot(
            enabled=self._enabled,
            daily_at=self._daily_at,
            next_run=self._next_run,
            running=self._running,
            last_fired_at=self._last_fired_at,
            last_outcome=self._last_outcome,
            last_reason=self._last_reason,
        )

    async def tick(self) -> bool:
        """Re-read config and fire if today's slot is due and unfired. True when it fired."""
        config = self._config_provider()
        automation = config.automation
        self._enabled = automation.enabled
        self._daily_at = automation.daily_at if automation.enabled else None
        if not automation.enabled:
            self._next_run = None
            return False

        now = self._clock()
        slot = _slot_today(now, automation.daily_at)
        if now >= slot and self._last_fired_date != now.date():
            # WHY: the durable attempt table is the source of truth across restarts and
            # `docker exec … auto run` — one automation decision per calendar day.
            self._last_fired_date = await asyncio.to_thread(_last_attempt_local_date, config, now)
        if now < slot or self._last_fired_date == now.date():
            self._next_run = slot if now < slot else slot + timedelta(days=1)
            return False

        self._last_fired_date = now.date()
        self._last_fired_at = now
        self._next_run = slot + timedelta(days=1)
        await self._fire(config)
        return True

    async def _fire(self, config: Config) -> None:
        self._running = True
        try:
            result = await asyncio.to_thread(self._run_once, config)
        except Exception as exc:
            # WHY: only the exception type is kept — messages can carry paths or secrets
            # and this lands in /health.
            self._last_outcome = "error"
            self._last_reason = type(exc).__name__
            logger.exception("In-process automation run failed")
        else:
            self._last_outcome = result.outcome.value
            self._last_reason = result.reason
            logger.info("In-process automation: %s (%s)", result.outcome.value, result.reason)
        finally:
            self._running = False

    async def run_forever(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        """Poll forever; a failing tick is logged and retried at the next poll."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("In-process automation tick failed")
            await sleep(POLL_SECONDS)


def _last_attempt_local_date(config: Config, now: datetime) -> date | None:
    from immich_memories.automation.state_store import AutomationStateStore

    last = AutomationStateStore(config.cache.database_path).get_last_attempt()
    if last is None:
        return None
    return last.started_at.astimezone(now.tzinfo).date()


def _run_auto_once(config: Config) -> AutoRunResult:
    from immich_memories.automation.runner import AutoRunner

    return AutoRunner(config).run_one()


def _current_config() -> Config:
    # WHY: resolved per tick so a config reloaded by the settings page changes the schedule.
    from immich_memories.config import get_config

    return get_config()


# The UI process's single timer; `ui/app.py` starts it and `/health` reads its snapshot.
automation_scheduler = InProcessScheduler(_current_config)
