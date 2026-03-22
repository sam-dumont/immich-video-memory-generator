"""Scheduler daemon — foreground loop that sleeps until next job, then fires.

Usage:
    immich-memories scheduler start --foreground
"""

from __future__ import annotations

import contextlib
import logging
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from immich_memories.scheduling.engine import PendingJob, Scheduler
from immich_memories.scheduling.executor import resolve_schedule_params
from immich_memories.scheduling.models import SchedulerConfig

logger = logging.getLogger(__name__)

# Flag for graceful shutdown via SIGTERM
_shutdown_requested = False


def _handle_signal(signum, frame):
    """Set shutdown flag on SIGINT/SIGTERM."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info(f"Received signal {signum}, shutting down...")


def run_daemon_loop(config: SchedulerConfig, *, db_path: Path) -> None:
    """Run the scheduler daemon in the foreground.

    Sleeps until the next job fires, executes it via CLI subprocess,
    then recalculates. Handles SIGINT/SIGTERM for graceful shutdown.
    """
    global _shutdown_requested
    _shutdown_requested = False

    signal.signal(signal.SIGTERM, _handle_signal)

    # Clean up any runs left in 'running' state from a previous crash
    from immich_memories.tracking.run_database import RunDatabase

    db = RunDatabase(db_path=db_path)
    db.mark_stale_runs_as_interrupted()

    scheduler = Scheduler(config)
    logger.info(f"Scheduler daemon started ({len(config.schedules)} schedules)")

    with contextlib.suppress(KeyboardInterrupt):
        while not _shutdown_requested:
            now = datetime.now(tz=UTC)
            wait = scheduler.seconds_until_next(now)

            if wait is None:
                logger.info("No enabled schedules, sleeping 60s")
                time.sleep(60)
                continue

            jobs = scheduler.get_next_jobs(now)
            next_job = jobs[0]
            logger.info(
                f"Next: '{next_job.schedule.name}' at "
                f"{next_job.fire_time.strftime('%Y-%m-%d %H:%M UTC')} "
                f"(in {wait:.0f}s)"
            )

            # Sleep until job fires (check shutdown flag periodically)
            sleep_end = time.monotonic() + wait
            while time.monotonic() < sleep_end and not _shutdown_requested:
                remaining = sleep_end - time.monotonic()
                time.sleep(min(remaining, 30))

            if _shutdown_requested:
                break

            # Execute the job
            execute_job(next_job, timeout_seconds=config.job_timeout_minutes * 60)

    logger.info("Scheduler daemon stopped")


def execute_job(job: PendingJob, timeout_seconds: int = 3600) -> None:
    """Execute a scheduled job by invoking the CLI as a subprocess."""
    params = resolve_schedule_params(job.schedule, job.fire_time)
    logger.info(f"Executing '{job.schedule.name}': {params}")

    cmd = ["immich-memories", "generate"]
    cmd.extend(["--memory-type", params["memory_type"]])

    if "year" in params:
        cmd.extend(["--year", str(params["year"])])
    if "month" in params:
        cmd.extend(["--month", str(params["month"])])
    if "target_date" in params:
        cmd.extend(["--target-date", str(params["target_date"])])
    if params.get("upload_to_immich"):
        cmd.append("--upload-to-immich")
    if params.get("album_name"):
        cmd.extend(["--album", params["album_name"]])
    if params.get("duration_minutes"):
        cmd.extend(["--duration", str(params["duration_minutes"])])
    for name in params.get("person_names", []):
        cmd.extend(["--person", name])

    logger.info(f"Running: {' '.join(cmd)}")
    start = time.monotonic()
    error_msg: str | None = None
    success = False

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        error_msg = f"Timed out after {timeout_seconds // 60} minutes"
        logger.error(f"Job '{job.schedule.name}' {error_msg}")
    else:
        if result.returncode == 0:
            success = True
            logger.info(f"Job '{job.schedule.name}' completed successfully")
        else:
            error_msg = result.stderr[-500:] if result.stderr else "no stderr"
            logger.error(
                f"Job '{job.schedule.name}' failed (exit {result.returncode}): {error_msg}"
            )

    elapsed = time.monotonic() - start
    _notify_if_configured(
        memory_type=params["memory_type"],
        success=success,
        duration_seconds=elapsed,
        error=error_msg,
    )


def _notify_if_configured(
    memory_type: str,
    success: bool,
    duration_seconds: float = 0.0,
    error: str | None = None,
) -> None:
    """Send an Apprise notification if enabled in config."""
    from immich_memories.config_loader import get_config

    try:
        config = get_config()
    except Exception:
        return

    notif = config.notifications
    if not notif.enabled or not notif.urls:
        return
    status = "completed" if success else "failed"
    if (success and not notif.on_success) or (not success and not notif.on_failure):
        return

    from immich_memories.automation.notifications import notify_job_complete

    notify_job_complete(
        memory_type=memory_type,
        status=status,
        duration_seconds=duration_seconds,
        error=error,
        urls=notif.urls,
    )
