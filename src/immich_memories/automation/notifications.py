"""Notify on job completion via Apprise (130+ notification backends)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.automation.notification_state import (
    NotificationFailureCategory,
    NotificationStateStore,
)

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def send_configured_notification(
    config: Config,
    memory_type: str,
    success: bool,
    duration_seconds: float = 0.0,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    """Send one enabled success/failure notification using the shared policy."""
    notif = config.notifications
    if not notif.enabled or not notif.urls:
        return
    status = "completed" if success else "failed"
    if (success and not notif.on_success) or (not success and not notif.on_failure):
        return
    notify_job_complete(
        memory_type=memory_type,
        status=status,
        duration_seconds=duration_seconds,
        output_path=output_path,
        error=error,
        urls=notif.urls,
        db_path=config.cache.database_path,
        attach_thumbnail=notif.attach_thumbnail,
        cooldown_hours=notif.cooldown_hours,
    )


def notify_job_complete(
    memory_type: str,
    status: str,
    duration_seconds: float = 0.0,
    output_path: str | None = None,
    error: str | None = None,
    urls: list[str] | None = None,
    db_path: Path | None = None,
    attach_thumbnail: bool = False,
    cooldown_hours: int = 24,
    bypass_cooldown: bool = False,
) -> bool:
    """Send a notification about job completion via Apprise.

    Returns True if at least one notification was delivered, False otherwise.
    Fails silently (logs warning) if the apprise package is not installed.
    """
    if not urls:
        return False

    state = _get_state_store(db_path)
    if state is not None and not bypass_cooldown and state.is_cooling_down(cooldown_hours):
        logger.warning("Notification delivery suppressed during failure cooldown")
        return False

    try:
        import apprise
    except ImportError:
        logger.warning("apprise not installed — skipping notification (pip install apprise)")
        _record_failure(state, NotificationFailureCategory.UNAVAILABLE)
        return False

    title = _build_title(memory_type, status)
    body = _build_body(memory_type, status, duration_seconds, output_path, error)

    attach = (
        _extract_thumbnail(output_path)
        if attach_thumbnail and output_path and status == "completed"
        else None
    )

    try:
        apobj = apprise.Apprise()
        for url in urls:
            apobj.add(url)
        kwargs: dict = {"title": title, "body": body}
        if attach:
            kwargs["attach"] = attach
        result = bool(apobj.notify(**kwargs))
    except Exception as exc:  # WHY: notification delivery is always best-effort
        category = _classify_failure(exc)
        logger.warning("Notification delivery error (%s)", category.value)
        _record_failure(state, category)
        return False
    finally:
        if attach:
            _cleanup_thumbnail(attach)

    if result:
        logger.info("Notification sent: %s", title)
        _record_success(state)
    else:
        logger.warning("Notification delivery failed: %s", title)
        _record_failure(state, NotificationFailureCategory.PROVIDER_REJECTED)
    return result


def _get_state_store(db_path: Path | None) -> NotificationStateStore | None:
    """Open optional durable state without making notifications depend on SQLite."""
    if db_path is None:
        return None
    try:
        return NotificationStateStore(Path(db_path))
    except (OSError, RuntimeError, sqlite3.Error):
        logger.warning("Notification health state is unavailable")
        return None


def _record_success(state: NotificationStateStore | None) -> None:
    if state is None:
        return
    try:
        state.record_success()
    except (OSError, RuntimeError, sqlite3.Error):
        logger.warning("Could not persist notification success state")


def _record_failure(
    state: NotificationStateStore | None,
    category: NotificationFailureCategory,
) -> None:
    if state is None:
        return
    try:
        state.record_failure(category)
    except (OSError, RuntimeError, sqlite3.Error):
        logger.warning("Could not persist notification failure state")


def _classify_failure(exc: Exception) -> NotificationFailureCategory:
    """Classify in memory; only the generic category is ever persisted or logged."""
    text = str(exc).casefold()
    if any(token in text for token in ("429", "quota", "rate limit", "too many requests")):
        return NotificationFailureCategory.QUOTA
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "auth")):
        return NotificationFailureCategory.AUTH
    return NotificationFailureCategory.TRANSPORT


def _extract_thumbnail(output_path: str) -> str | None:
    """Extract a thumbnail frame from the output video for notification attachment."""
    import subprocess
    import tempfile
    from pathlib import Path

    video = Path(output_path)
    if not video.exists():
        return None

    thumb = Path(tempfile.gettempdir()) / f"immich_notif_{video.stem}.jpg"
    try:
        # WHY: seek to 25% of video for a representative frame (skips title screen)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "5",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-1",
                "-q:v",
                "4",
                str(thumb),
            ],
            capture_output=True,
            timeout=10,
        )
        if thumb.exists() and thumb.stat().st_size > 0:
            return str(thumb)
    except (OSError, subprocess.SubprocessError):
        logger.debug("Failed to extract notification thumbnail")
    return None


def _cleanup_thumbnail(path: str) -> None:
    """Remove temporary thumbnail file."""
    import contextlib
    from pathlib import Path

    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)


def _build_title(memory_type: str, status: str) -> str:
    label = "Memory Generated" if status == "completed" else "Generation Failed"
    return f"{label}: {memory_type.replace('_', ' ').title()}"


def _build_body(
    memory_type: str,
    status: str,
    duration_seconds: float,
    output_path: str | None,
    error: str | None,
) -> str:
    lines = [f"Type: {memory_type}"]
    if duration_seconds > 0:
        mins = int(duration_seconds // 60)
        secs = int(duration_seconds % 60)
        lines.append(f"Processing time: {mins}m {secs:02d}s")
    if output_path and status == "completed":
        lines.append(f"Output: {output_path}")
    if error and status == "failed":
        lines.append(f"Error: {error[:200]}")
    return "\n".join(lines)


def send_test_notification(
    urls: list[str],
    *,
    db_path: Path | None = None,
    attach_thumbnail: bool = False,
    cooldown_hours: int = 24,
) -> bool:
    """Send a test notification to verify Apprise URL configuration."""
    return notify_job_complete(
        memory_type="test",
        status="completed",
        urls=urls,
        db_path=db_path,
        attach_thumbnail=attach_thumbnail,
        cooldown_hours=cooldown_hours,
        bypass_cooldown=True,
    )
