"""Tests for the Daemon/Scheduler feature."""

from __future__ import annotations

from datetime import UTC, datetime


class TestScheduleEntryConfig:
    """Slice 1: Schedule entry and scheduler config models."""

    def test_schedule_entry_defaults(self):
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="yearly", memory_type="year_in_review", cron="0 9 15 1 *")
        assert entry.name == "yearly"
        assert entry.memory_type == "year_in_review"
        assert entry.cron == "0 9 15 1 *"
        assert entry.enabled
        assert not entry.upload_to_immich
        assert entry.album_name is None
        assert not entry.person_names
        assert entry.duration_minutes is None

    def test_schedule_entry_full_config(self):
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(
            name="monthly",
            memory_type="monthly_highlights",
            cron="0 9 1 * *",
            enabled=True,
            upload_to_immich=True,
            album_name="{year} Memories",
            person_names=["Alice"],
            duration_minutes=3,
            params={"month": 7, "year": 2024},
        )
        assert entry.album_name == "{year} Memories"
        assert entry.person_names == ["Alice"]
        assert entry.params == {"month": 7, "year": 2024}

    def test_scheduler_config_defaults(self):
        from immich_memories.scheduling.models import SchedulerConfig

        config = SchedulerConfig()
        assert not config.enabled
        assert config.timezone == "UTC"
        assert not config.schedules

    def test_scheduler_config_with_schedules(self):
        from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

        config = SchedulerConfig(
            enabled=True,
            timezone="America/New_York",
            schedules=[
                ScheduleEntry(name="yearly", memory_type="year_in_review", cron="0 9 15 1 *"),
                ScheduleEntry(name="daily", memory_type="on_this_day", cron="0 9 * * *"),
            ],
        )
        assert config.enabled
        assert len(config.schedules) == 2

    def test_scheduler_config_in_main_config(self):
        """SchedulerConfig should be accessible from the main Config object."""
        from immich_memories.config_loader import Config

        config = Config()
        assert hasattr(config, "scheduler")
        assert not config.scheduler.enabled
        assert not config.scheduler.schedules


class TestSchedulerEngine:
    """Slice 2: Scheduler engine — next-run calculation and job queue."""

    def test_next_run_time(self):
        """Should calculate next run time from cron expression."""
        from immich_memories.scheduling.engine import Scheduler
        from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

        config = SchedulerConfig(
            enabled=True,
            timezone="UTC",
            schedules=[
                ScheduleEntry(name="daily", memory_type="on_this_day", cron="0 9 * * *"),
            ],
        )
        scheduler = Scheduler(config)
        now = datetime(2024, 7, 15, 8, 0, 0, tzinfo=UTC)

        next_jobs = scheduler.get_next_jobs(now)

        assert len(next_jobs) == 1
        assert next_jobs[0].schedule.name == "daily"
        # Next 9:00 AM UTC after 8:00 AM is same day
        assert next_jobs[0].fire_time == datetime(2024, 7, 15, 9, 0, 0, tzinfo=UTC)

    def test_skips_disabled_schedules(self):
        from immich_memories.scheduling.engine import Scheduler
        from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

        config = SchedulerConfig(
            enabled=True,
            schedules=[
                ScheduleEntry(name="active", memory_type="on_this_day", cron="0 9 * * *"),
                ScheduleEntry(
                    name="disabled", memory_type="year_in_review", cron="0 9 15 1 *", enabled=False
                ),
            ],
        )
        scheduler = Scheduler(config)
        now = datetime(2024, 7, 15, 8, 0, 0, tzinfo=UTC)

        next_jobs = scheduler.get_next_jobs(now)

        assert len(next_jobs) == 1
        assert next_jobs[0].schedule.name == "active"

    def test_sorted_by_fire_time(self):
        """Jobs should be sorted by fire time (earliest first)."""
        from immich_memories.scheduling.engine import Scheduler
        from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

        config = SchedulerConfig(
            enabled=True,
            schedules=[
                # Yearly: Jan 15 at 9:00
                ScheduleEntry(name="yearly", memory_type="year_in_review", cron="0 9 15 1 *"),
                # Daily at 9:00
                ScheduleEntry(name="daily", memory_type="on_this_day", cron="0 9 * * *"),
            ],
        )
        scheduler = Scheduler(config)
        now = datetime(2024, 7, 15, 8, 0, 0, tzinfo=UTC)

        next_jobs = scheduler.get_next_jobs(now)

        assert len(next_jobs) == 2
        # Daily fires today, yearly fires next Jan 15
        assert next_jobs[0].schedule.name == "daily"
        assert next_jobs[1].schedule.name == "yearly"
        assert next_jobs[0].fire_time < next_jobs[1].fire_time

    def test_seconds_until_next(self):
        """Should calculate seconds until next job fires."""
        from immich_memories.scheduling.engine import Scheduler
        from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig

        config = SchedulerConfig(
            enabled=True,
            schedules=[
                ScheduleEntry(name="daily", memory_type="on_this_day", cron="0 9 * * *"),
            ],
        )
        scheduler = Scheduler(config)
        now = datetime(2024, 7, 15, 8, 0, 0, tzinfo=UTC)

        wait = scheduler.seconds_until_next(now)

        assert wait == 3600.0  # 1 hour until 9:00


class TestJobExecutor:
    """Slice 3: Resolve schedule entries into preset params for generation."""

    def test_resolve_yearly_review(self):
        """Yearly review should auto-fill year from fire time."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="yearly", memory_type="year_in_review", cron="0 9 15 1 *")
        # Fired on Jan 15 2025 → generate for previous year (2024)
        fire_time = datetime(2025, 1, 15, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["memory_type"] == "year_in_review"
        assert params["year"] == 2024  # previous year

    def test_resolve_monthly_highlights(self):
        """Monthly highlights should auto-fill year and month from fire time."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="monthly", memory_type="monthly_highlights", cron="0 9 1 * *")
        # Fired on Aug 1 2024 → generate for previous month (July 2024)
        fire_time = datetime(2024, 8, 1, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["memory_type"] == "monthly_highlights"
        assert params["year"] == 2024
        assert params["month"] == 7  # previous month

    def test_resolve_monthly_highlights_january(self):
        """January fire → should generate for December of previous year."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="monthly", memory_type="monthly_highlights", cron="0 9 1 * *")
        fire_time = datetime(2025, 1, 1, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["year"] == 2024
        assert params["month"] == 12

    def test_resolve_on_this_day(self):
        """On This Day should use fire date as the target."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="daily", memory_type="on_this_day", cron="0 9 * * *")
        fire_time = datetime(2024, 7, 15, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["memory_type"] == "on_this_day"
        assert params["target_date"].month == 7
        assert params["target_date"].day == 15

    def test_explicit_params_override(self):
        """Explicit params in config should override auto-resolved ones."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(
            name="specific",
            memory_type="year_in_review",
            cron="0 9 15 1 *",
            params={"year": 2020},  # explicit override
        )
        fire_time = datetime(2025, 1, 15, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["year"] == 2020  # explicit wins over auto-resolved

    def test_resolve_trip(self):
        """Trip should auto-fill year as previous year (same as year_in_review)."""
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(name="trips", memory_type="trip", cron="0 9 15 1 *")
        fire_time = datetime(2025, 1, 15, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["memory_type"] == "trip"
        assert params["year"] == 2024  # previous year

    def test_person_names_passed_through(self):
        from immich_memories.scheduling.executor import resolve_schedule_params
        from immich_memories.scheduling.models import ScheduleEntry

        entry = ScheduleEntry(
            name="alice",
            memory_type="person_spotlight",
            cron="0 9 15 1 *",
            person_names=["Alice"],
        )
        fire_time = datetime(2025, 1, 15, 9, 0, 0, tzinfo=UTC)

        params = resolve_schedule_params(entry, fire_time)

        assert params["person_names"] == ["Alice"]


class TestSchedulerCLI:
    """Slice 4: CLI commands for the scheduler."""

    def test_scheduler_group_exists(self):
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["scheduler", "--help"])
        assert result.exit_code == 0
        assert "scheduler" in result.output.lower()

    def test_scheduler_list_empty(self):
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["scheduler", "list"])
        assert result.exit_code == 0
        assert "No schedules" in result.output

    def test_scheduler_status(self):
        from click.testing import CliRunner

        from immich_memories.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["scheduler", "status"])
        assert result.exit_code == 0
        # Should indicate disabled since default config has scheduler.enabled=False
        assert "disabled" in result.output.lower() or "not enabled" in result.output.lower()
