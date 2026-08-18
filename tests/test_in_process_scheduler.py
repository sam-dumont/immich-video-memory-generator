"""In-process daily automation timer for the UI/Docker process (#305)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from immich_memories.config_models import AutomationConfig


class TestDailyAtConfig:
    def test_defaults_are_off_and_nine_am(self) -> None:
        auto = AutomationConfig()

        assert auto.enabled is False
        assert auto.daily_at == "09:00"

    @pytest.mark.parametrize("value", ["25:00", "09:60", "9am", "", "09:00:00"])
    def test_rejects_times_that_are_not_hh_mm(self, value: str) -> None:
        with pytest.raises(ValidationError):
            AutomationConfig(daily_at=value)

    def test_normalizes_single_digit_hour(self) -> None:
        assert AutomationConfig(daily_at="9:05").daily_at == "09:05"


# ---------------------------------------------------------------------------
# Scheduler behaviour
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from immich_memories.automation.in_process_scheduler import InProcessScheduler  # noqa: E402
from immich_memories.automation.models import AutoOutcome, AutoRunResult  # noqa: E402
from immich_memories.config_loader import Config  # noqa: E402

LOCAL = datetime.now().astimezone().tzinfo


def _config(tmp_path: Path, **automation: object) -> Config:
    return Config(
        immich={"url": "http://immich.test:2283", "api_key": "test-key"},
        cache={"database": str(tmp_path / "test.db"), "directory": str(tmp_path / "cache")},
        automation=automation,
    )


class _FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def _completed(config: Config) -> AutoRunResult:
    return AutoRunResult(outcome=AutoOutcome.COMPLETED, reason="generated")


class TestTick:
    async def test_disabled_scheduler_never_fires(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=False, daily_at="09:00")
        fired: list[Config] = []
        clock = _FakeClock(datetime(2026, 8, 18, 9, 0, 5, tzinfo=LOCAL))
        scheduler = InProcessScheduler(
            lambda: config, run_once=lambda c: fired.append(c) or _completed(c), clock=clock
        )

        assert await scheduler.tick() is False

        assert fired == []
        snap = scheduler.snapshot()
        assert snap.enabled is False
        assert snap.next_run is None

    async def test_enabled_before_slot_reports_next_run_today(self, tmp_path: Path) -> None:
        config = _config(tmp_path, enabled=True, daily_at="09:00")
        fired: list[Config] = []
        clock = _FakeClock(datetime(2026, 8, 18, 8, 59, tzinfo=LOCAL))
        scheduler = InProcessScheduler(
            lambda: config, run_once=lambda c: fired.append(c) or _completed(c), clock=clock
        )

        assert await scheduler.tick() is False

        assert fired == []
        snap = scheduler.snapshot()
        assert snap.enabled is True
        assert snap.daily_at == "09:00"
        assert snap.next_run == datetime(2026, 8, 18, 9, 0, tzinfo=LOCAL)

    async def test_fires_once_when_slot_arrives_then_waits_for_tomorrow(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path, enabled=True, daily_at="09:00")
        fired: list[Config] = []
        clock = _FakeClock(datetime(2026, 8, 18, 8, 59, 50, tzinfo=LOCAL))
        scheduler = InProcessScheduler(
            lambda: config, run_once=lambda c: fired.append(c) or _completed(c), clock=clock
        )
        assert await scheduler.tick() is False

        clock.advance(seconds=25)  # 09:00:15 — one poll interval late, still today's slot
        assert await scheduler.tick() is True
        clock.advance(minutes=30)
        assert await scheduler.tick() is False
        clock.advance(hours=12)
        assert await scheduler.tick() is False

        assert fired == [config]
        snap = scheduler.snapshot()
        assert snap.last_fired_at == datetime(2026, 8, 18, 9, 0, 15, tzinfo=LOCAL)
        assert snap.last_outcome == "completed"
        assert snap.last_reason == "generated"
        assert snap.running is False
        assert snap.next_run == datetime(2026, 8, 19, 9, 0, tzinfo=LOCAL)


class TestRestartCatchUp:
    """A container that was down at daily_at behaves like systemd Persistent=true."""

    async def test_starts_after_slot_with_no_attempt_today_fires_immediately(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path, enabled=True, daily_at="09:00")
        fired: list[Config] = []
        clock = _FakeClock(datetime(2026, 8, 18, 14, 30, tzinfo=LOCAL))
        scheduler = InProcessScheduler(
            lambda: config, run_once=lambda c: fired.append(c) or _completed(c), clock=clock
        )

        assert await scheduler.tick() is True
        assert fired == [config]

    async def test_starts_after_slot_with_an_attempt_already_today_waits_for_tomorrow(
        self, tmp_path: Path
    ) -> None:
        from immich_memories.automation.state_store import AutomationStateStore

        config = _config(tmp_path, enabled=True, daily_at="09:00")
        # The durable attempt row is what a pre-restart fire (or `docker exec … auto run`)
        # leaves behind; started_at is "now" in UTC, i.e. today.
        AutomationStateStore(config.cache.database_path).start_attempt(reason="daily wake")
        fired: list[Config] = []
        now = datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)
        clock = _FakeClock(now)
        scheduler = InProcessScheduler(
            lambda: config, run_once=lambda c: fired.append(c) or _completed(c), clock=clock
        )

        assert await scheduler.tick() is False

        assert fired == []
        assert scheduler.snapshot().next_run == now.replace(hour=9) + timedelta(days=1)


class TestFailureIsolation:
    async def test_crashing_run_is_recorded_by_type_only_and_not_retried_today(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path, enabled=True, daily_at="09:00")
        calls = 0

        def boom(_config: Config) -> AutoRunResult:
            nonlocal calls
            calls += 1
            raise RuntimeError("api key hunter2 leaked in message")

        clock = _FakeClock(datetime(2026, 8, 18, 9, 0, 1, tzinfo=LOCAL))
        scheduler = InProcessScheduler(lambda: config, run_once=boom, clock=clock)

        assert await scheduler.tick() is True
        clock.advance(hours=1)
        assert await scheduler.tick() is False

        assert calls == 1
        snap = scheduler.snapshot()
        assert snap.last_outcome == "error"
        assert snap.last_reason == "RuntimeError"
        assert "hunter2" not in str(snap.to_dict())
        assert snap.running is False

    async def test_run_forever_survives_a_failing_tick_and_keeps_polling(
        self, tmp_path: Path
    ) -> None:
        good = _config(tmp_path, enabled=False)
        providers = iter([RuntimeError("config unreadable"), good, good])

        def provider() -> Config:
            item = next(providers)
            if isinstance(item, Exception):
                raise item
            return item

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise asyncio.CancelledError

        scheduler = InProcessScheduler(provider)

        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_forever(sleep=fake_sleep)

        assert len(sleeps) == 3
        assert scheduler.snapshot().enabled is False
