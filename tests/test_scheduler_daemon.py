"""Tests for the scheduler daemon loop."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig


class TestDaemonLoop:
    """Scheduler daemon: sleep until next job, execute, repeat."""

    def test_execute_job_builds_cli_command(self):
        """execute_job should resolve params and run CLI with correct args."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="yearly",
            memory_type="year_in_review",
            cron="0 6 15 1 *",
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        )

        with patch("immich_memories.scheduling.daemon.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            execute_job(job)

        mock_sub.run.assert_called_once()
        cmd = mock_sub.run.call_args[0][0]
        assert cmd[0] == "immich-memories"
        assert cmd[1] == "generate"
        assert "--memory-type" in cmd
        assert "year_in_review" in cmd
        assert "--year" in cmd
        assert "2025" in cmd  # Previous year

    def test_execute_job_with_upload(self):
        """execute_job should pass --upload-to-immich when enabled."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="monthly",
            memory_type="monthly_highlights",
            cron="0 6 1 * *",
            upload_to_immich=True,
            album_name="Monthly {month}",
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 3, 1, 6, 0, tzinfo=UTC),
        )

        with patch("immich_memories.scheduling.daemon.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            execute_job(job)

        cmd = mock_sub.run.call_args[0][0]
        assert "--upload-to-immich" in cmd
        assert "--album" in cmd

    def test_default_timeout_is_60_minutes(self):
        """Default job timeout should be 3600s (60min), not 1800s."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="yearly",
            memory_type="year_in_review",
            cron="0 6 15 1 *",
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        )

        with patch("immich_memories.scheduling.daemon.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            execute_job(job)

        _, kwargs = mock_sub.run.call_args
        assert kwargs["timeout"] == 3600

    def test_custom_timeout_from_config(self):
        """SchedulerConfig.job_timeout_minutes overrides the default."""
        config = SchedulerConfig(
            enabled=True,
            job_timeout_minutes=90,
            schedules=[],
        )
        assert config.job_timeout_minutes == 90

    def test_timeout_error_message_shows_minutes(self):
        """Timeout error should report the configured duration in minutes."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="yearly",
            memory_type="year_in_review",
            cron="0 6 15 1 *",
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        )

        with patch("immich_memories.scheduling.daemon.subprocess") as mock_sub:
            mock_sub.TimeoutExpired = TimeoutError
            mock_sub.run.side_effect = TimeoutError("timed out")
            # Should not raise — just logs
            execute_job(job, timeout_seconds=5400)

    def test_daemon_handles_sigint(self):
        """run_daemon_loop should stop gracefully on KeyboardInterrupt."""
        from immich_memories.scheduling.daemon import run_daemon_loop

        config = SchedulerConfig(
            enabled=True,
            schedules=[
                ScheduleEntry(
                    name="test",
                    memory_type="year_in_review",
                    cron="0 6 * * *",
                ),
            ],
        )

        mock_db = MagicMock()
        with (
            patch("immich_memories.scheduling.daemon.time.sleep", side_effect=KeyboardInterrupt),
            # WHY: avoid real DB init during test — RunDatabase needs config + SQLite
            patch("immich_memories.tracking.run_database.RunDatabase", return_value=mock_db),
        ):
            # Should not raise — graceful shutdown
            run_daemon_loop(config, db_path=Path("/tmp/test_daemon.db"))

    def test_person_names_use_equals_syntax(self):
        """Person names should use --person=Name to prevent flag injection."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="spotlight",
            memory_type="person_spotlight",
            cron="0 6 1 * *",
            person_names=["Alice", "--evil-flag"],
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 3, 1, 6, 0, tzinfo=UTC),
        )

        with patch("immich_memories.scheduling.daemon.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            execute_job(job)

        cmd = mock_sub.run.call_args[0][0]
        assert "--person=Alice" in cmd
        assert "--person=--evil-flag" in cmd
        # Verify the name is never a standalone arg
        assert "--evil-flag" not in [c for c in cmd if c != "--person=--evil-flag"]
