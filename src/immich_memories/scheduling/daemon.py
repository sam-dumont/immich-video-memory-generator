"""Scheduler daemon — foreground loop that sleeps until next job, then fires.

Usage:
    immich-memories scheduler start --foreground
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from immich_memories.operations.bounded_process import ProcessCancelled, run_bounded_process
from immich_memories.scheduling.engine import PendingJob, Scheduler
from immich_memories.scheduling.executor import resolve_schedule_params
from immich_memories.scheduling.models import DEFAULT_JOB_TIMEOUT_MINUTES, SchedulerConfig
from immich_memories.security import sanitize_filename

logger = logging.getLogger(__name__)

# Flag for graceful shutdown via SIGTERM
_shutdown_requested = False


def _handle_signal(signum, frame):
    """Set shutdown flag on SIGINT/SIGTERM."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info(f"Received signal {signum}, shutting down...")


def run_daemon_loop(
    config: SchedulerConfig,
    *,
    db_path: Path,
    config_path: Path | None = None,
) -> None:
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
            execute_job(
                next_job,
                timeout_seconds=config.job_timeout_minutes * 60,
                config_path=config_path,
            )

    logger.info("Scheduler daemon stopped")


_STREAM_TAIL = 500


def describe_process_failure(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    """Summarise why a child process failed, from whichever stream carries it.

    The child logs to stdout -- `setup_logging` installs a StreamHandler there
    and `print_error` goes through Rich, also stdout -- so reading only stderr
    reported "no stderr" for every failure while the cause sat in the stream
    being discarded. A deadline exception may hand over raw bytes instead of
    the decoded capture.
    """
    out, err = _decoded(stdout), _decoded(stderr)
    parts = []
    if err.strip():
        parts.append(f"stderr: {err.strip()[-_STREAM_TAIL:]}")
    if out.strip():
        parts.append(f"stdout: {out.strip()[-_STREAM_TAIL:]}")
    if not parts:
        return "no output on stdout or stderr"
    return " | ".join(parts)


def _decoded(stream: str | bytes | None) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream or ""


def _child_log_path(schedule_name: str) -> Path:
    """Where a scheduled generation writes its own log.

    The daemon keeps a 500-character tail, which is enough to say what went
    wrong and never enough to work out why. FileHandler appends, so this is
    one growing file per schedule rather than one per firing.
    """
    safe = sanitize_filename(schedule_name) or "schedule"
    return Path.home() / ".immich-memories" / "logs" / f"generate-{safe}.log"


_SECONDS_PER_MINUTE = 60

# Params that come from the ScheduleEntry's own fields; each has its own flag
# below rather than a scope option.
_ENTRY_PARAMS = frozenset(
    {"memory_type", "person_names", "duration_minutes", "upload_to_immich", "album_name"}
)

# Every scope or selector a schedule can resolve, and the `generate` option
# that carries it. A param outside this table cannot reach the child at all.
_SCOPE_FLAGS = {
    "year": "--year",
    "month": "--month",
    "season": "--season",
    "holiday": "--holiday",
    "years_back": "--years-back",
    "trip_index": "--trip-index",
    "near_date": "--near-date",
}

# Any of these tells trip generation which trip to render; without one it lists
# what it found and renders nothing.
_TRIP_SELECTORS = ("trip_index", "month", "near_date")


class UnschedulableParam(ValueError):
    """A resolved schedule param that no `generate` option can express."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"schedule param '{key}' has no matching generate option, "
            f"so the run would silently ignore it"
        )


def _scope_arguments(params: dict) -> list[str]:
    """Translate the resolved scope into options, refusing to drop any of it."""
    arguments: list[str] = []
    for key, value in params.items():
        if key in _ENTRY_PARAMS:
            continue
        flag = _SCOPE_FLAGS.get(key)
        if flag is None:
            raise UnschedulableParam(key)
        arguments.extend([flag, str(value)])
    return arguments


def _generate_command(params: dict, config_path: Path | None) -> list[str]:
    cmd = ["immich-memories"]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    cmd.append("generate")
    memory_type = params["memory_type"]
    cmd.extend(["--memory-type", memory_type])
    cmd.extend(_scope_arguments(params))
    if memory_type == "trip" and not any(key in params for key in _TRIP_SELECTORS):
        # WHY: a trip run with no selector prints the trips it detected, renders
        # none of them and exits zero -- a scheduled trip that reports success
        # and produces nothing.
        cmd.append("--all-trips")
    if params.get("upload_to_immich"):
        cmd.append("--upload-to-immich")
    if params.get("album_name"):
        cmd.extend(["--album", params["album_name"]])
    if params.get("duration_minutes"):
        # WHY: the schedule states minutes and --duration takes seconds, so
        # `duration_minutes: 3` used to ask for a three-second video.
        cmd.extend(["--duration", str(params["duration_minutes"] * _SECONDS_PER_MINUTE)])
    for name in params.get("person_names", []):
        # WHY: `=` syntax prevents names starting with `-` from being parsed as flags
        cmd.append(f"--person={name}")
    return cmd


def _run_generation(
    cmd: list[str], *, name: str, timeout_seconds: int, child_env: dict[str, str]
) -> tuple[bool, str | None]:
    """Run one scheduled generation, returning whether it worked and why not."""
    try:
        result = run_bounded_process(
            cmd,
            timeout=timeout_seconds,
            env=child_env,
            cancel_check=lambda: _shutdown_requested,
        )
    except subprocess.TimeoutExpired as exc:
        error_msg = f"Timed out after {timeout_seconds // 60} minutes"
        if exc.stdout or exc.stderr:
            error_msg += f"; {describe_process_failure(exc.stdout, exc.stderr)}"
        logger.error(f"Job '{name}' {error_msg}")
        return False, error_msg
    except ProcessCancelled as exc:
        logger.info(f"Job '{name}' {exc}")
        return False, str(exc)
    if result.returncode == 0:
        logger.info(f"Job '{name}' completed successfully")
        return True, None
    error_msg = describe_process_failure(result.stdout, result.stderr)
    logger.error(f"Job '{name}' failed (exit {result.returncode}): {error_msg}")
    return False, error_msg


def execute_job(
    job: PendingJob,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_MINUTES * 60,
    *,
    config_path: Path | None = None,
) -> None:
    """Execute a scheduled job by invoking the CLI as a subprocess."""
    params = resolve_schedule_params(job.schedule, job.fire_time)
    logger.info(f"Executing '{job.schedule.name}': {params}")

    try:
        cmd = _generate_command(params, config_path)
    except UnschedulableParam as exc:
        logger.error(f"Job '{job.schedule.name}' cannot run: {exc}")
        _notify_if_configured(
            memory_type=params["memory_type"],
            success=False,
            error=str(exc),
            config_path=config_path,
        )
        return
    logger.info(f"Running: {' '.join(cmd)}")
    start = time.monotonic()

    # The child logs to its own file as well as to the pipes: the tail the
    # daemon keeps says what failed, this says why.
    child_env = os.environ.copy()
    log_path = _child_log_path(job.schedule.name)
    with contextlib.suppress(OSError):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        child_env["IMMICH_MEMORIES_LOG_FILE"] = str(log_path)

    success, error_msg = _run_generation(
        cmd,
        name=job.schedule.name,
        timeout_seconds=timeout_seconds,
        child_env=child_env,
    )
    _notify_if_configured(
        memory_type=params["memory_type"],
        success=success,
        duration_seconds=time.monotonic() - start,
        error=error_msg,
        config_path=config_path,
    )


def _notify_if_configured(
    memory_type: str,
    success: bool,
    duration_seconds: float = 0.0,
    error: str | None = None,
    config_path: Path | None = None,
) -> None:
    """Send an Apprise notification if enabled in config."""
    from immich_memories.config_loader import Config, get_config

    try:
        config = Config.from_yaml(config_path) if config_path is not None else get_config()
    except Exception:  # WHY: daemon top-level safety net — must not crash the scheduler
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
        db_path=config.cache.database_path,
        attach_thumbnail=notif.attach_thumbnail,
        cooldown_hours=notif.cooldown_hours,
    )
