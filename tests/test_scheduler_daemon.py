"""Tests for the scheduler daemon loop."""

from __future__ import annotations

from datetime import UTC, datetime
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

        with patch("immich_memories.scheduling.daemon.time.sleep", side_effect=KeyboardInterrupt):
            # Should not raise — graceful shutdown
            run_daemon_loop(config)
