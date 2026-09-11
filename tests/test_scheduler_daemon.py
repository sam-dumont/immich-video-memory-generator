"""Tests for the scheduler daemon loop."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from immich_memories.scheduling.models import ScheduleEntry, SchedulerConfig


class TestDaemonLoop:
    """Scheduler daemon: sleep until next job, execute, repeat."""

    @pytest.fixture(autouse=True)
    def _isolate_notifications(self):
        # WHY: _notify_if_configured would send a real notification/webhook on every test.
        with patch("immich_memories.scheduling.daemon._notify_if_configured"):
            yield

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

        # WHY: run_bounded_process would spawn the real immich-memories CLI as a subprocess.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_job(job)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "immich-memories"
        assert cmd[1] == "generate"
        assert "--memory-type" in cmd
        assert "year_in_review" in cmd
        assert "--year" in cmd
        assert "2025" in cmd  # Previous year

    def test_execute_job_propagates_custom_config_before_generate(self):
        """The legacy daemon must not fall back to the default Immich account."""
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
        config_path = Path("/tmp/Config dir/photos & family.yaml")

        # WHY: run_bounded_process would spawn the real subprocess; only the args are under test.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_job(job, config_path=config_path)

        assert mock_run.call_args.args[0][:4] == [
            "immich-memories",
            "--config",
            str(config_path),
            "generate",
        ]

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

        # WHY: run_bounded_process would spawn the subprocess; only --upload-to-immich matters.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_job(job)

        cmd = mock_run.call_args[0][0]
        assert "--upload-to-immich" in cmd
        assert "--album" in cmd

    def test_default_timeout_is_two_hours(self):
        """The complete generation gets the same finite budget as AutoRunner."""
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

        # WHY: run_bounded_process would spawn the real subprocess; only the timeout kwarg matters.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_job(job)

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == SchedulerConfig().job_timeout_minutes * 60 == 7200

    @pytest.mark.parametrize("minutes", [0, -1])
    def test_nonpositive_timeout_is_rejected(self, minutes):
        with pytest.raises(ValidationError):
            SchedulerConfig(job_timeout_minutes=minutes)

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

        # WHY: run_bounded_process would spawn the subprocess; side effect simulates a timeout.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("synthetic", 5400)
            # Should not raise — just logs
            execute_job(job, timeout_seconds=5400)

    def test_timeout_preserves_child_output_and_forwards_shutdown_check(self, monkeypatch, caplog):
        from immich_memories.scheduling import daemon
        from immich_memories.scheduling.engine import PendingJob

        monkeypatch.setattr(daemon, "_shutdown_requested", False)
        job = PendingJob(
            schedule=ScheduleEntry(name="yearly", memory_type="year_in_review", cron="0 6 15 1 *"),
            fire_time=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        )
        failure = subprocess.TimeoutExpired(
            "synthetic", 5400, output="last child progress", stderr="last child error"
        )
        with patch.object(daemon, "run_bounded_process", side_effect=failure) as run:
            daemon.execute_job(job, timeout_seconds=5400)
        check = run.call_args.kwargs["cancel_check"]
        assert not check()
        monkeypatch.setattr(daemon, "_shutdown_requested", True)
        assert check()
        assert "Timed out after 90 minutes" in caplog.text
        assert "last child progress" in caplog.text
        assert "last child error" in caplog.text

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
        # WHY: time.sleep and RunDatabase would block real time and touch real sqlite storage.
        with (
            # WHY: time.sleep is the daemon's poll wait; forced to raise instead of blocking.
            patch("immich_memories.scheduling.daemon.time.sleep", side_effect=KeyboardInterrupt),
            # WHY: avoid real DB init during test — RunDatabase needs config + SQLite
            patch("immich_memories.tracking.run_database.RunDatabase", return_value=mock_db),
        ):
            # Should not raise — graceful shutdown
            run_daemon_loop(config, db_path=Path("/tmp/test_daemon.db"))

    def test_daemon_loop_forwards_custom_config_to_each_job(self):
        """The daemon handoff cannot discard provenance after CLI startup."""
        import immich_memories.scheduling.daemon as daemon
        from immich_memories.scheduling.daemon import run_daemon_loop
        from immich_memories.scheduling.engine import PendingJob

        config = SchedulerConfig(
            enabled=True,
            job_timeout_minutes=90,
            schedules=[
                ScheduleEntry(
                    name="yearly",
                    memory_type="year_in_review",
                    cron="0 6 * * *",
                )
            ],
        )
        job = PendingJob(
            schedule=config.schedules[0],
            fire_time=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        )
        scheduler = MagicMock()
        scheduler.seconds_until_next.return_value = 0
        scheduler.get_next_jobs.return_value = [job]
        config_path = Path("/tmp/Config dir/family.yaml")

        def stop_after_job(*_args: object, **_kwargs: object) -> None:
            daemon._shutdown_requested = True

        # WHY: Scheduler, RunDatabase, and execute_job all touch real state; all three replaced.
        with (
            # WHY: Scheduler owns real wait-until-next-job timing; replaced with a scripted stub.
            patch("immich_memories.scheduling.daemon.Scheduler", return_value=scheduler),
            # WHY: RunDatabase would open a real sqlite file; replaced to avoid disk writes.
            patch("immich_memories.tracking.run_database.RunDatabase"),
            # WHY: execute_job would launch the real subprocess; stubbed to stop the loop.
            patch(
                "immich_memories.scheduling.daemon.execute_job",
                side_effect=stop_after_job,
            ) as execute,
        ):
            run_daemon_loop(
                config,
                db_path=Path("/tmp/test_daemon.db"),
                config_path=config_path,
            )

        execute.assert_called_once_with(
            job,
            timeout_seconds=5400,
            config_path=config_path,
        )

    def test_person_names_use_equals_syntax(self):
        """Person names should use --person=Name to prevent flag injection."""
        from immich_memories.scheduling.daemon import execute_job
        from immich_memories.scheduling.engine import PendingJob

        entry = ScheduleEntry(
            name="spotlight",
            memory_type="person_spotlight",
            cron="0 6 1 * *",
            person_names=["Riley", "--evil-flag"],
        )
        job = PendingJob(
            schedule=entry,
            fire_time=datetime(2026, 3, 1, 6, 0, tzinfo=UTC),
        )

        # WHY: run_bounded_process would spawn the real subprocess; only the argv shape is checked.
        with patch("immich_memories.scheduling.daemon.run_bounded_process") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_job(job)

        cmd = mock_run.call_args[0][0]
        assert "--person=Riley" in cmd
        assert "--person=--evil-flag" in cmd
        # Verify the name is never a standalone arg
        assert "--evil-flag" not in [c for c in cmd if c != "--person=--evil-flag"]
