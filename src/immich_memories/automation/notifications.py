"""Notify on job completion via Apprise (130+ notification backends)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify_job_complete(
    memory_type: str,
    status: str,
    duration_seconds: float = 0.0,
    output_path: str | None = None,
    error: str | None = None,
    urls: list[str] | None = None,
) -> bool:
    """Send a notification about job completion via Apprise.

    Returns True if at least one notification was delivered, False otherwise.
    Fails silently (logs warning) if the apprise package is not installed.
    """
    if not urls:
        return False

    try:
        import apprise
    except ImportError:
        logger.warning("apprise not installed — skipping notification (pip install apprise)")
        return False

    title = _build_title(memory_type, status)
    body = _build_body(memory_type, status, duration_seconds, output_path, error)

    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)

    try:
        result = bool(apobj.notify(title=title, body=body))
    except (OSError, RuntimeError) as e:
        logger.exception("Notification delivery error: %s", e)
        return False

    if result:
        logger.info("Notification sent: %s", title)
    else:
        logger.warning("Notification delivery failed: %s", title)
    return result


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


def send_test_notification(urls: list[str]) -> bool:
    """Send a test notification to verify Apprise URL configuration."""
    return notify_job_complete(
        memory_type="test",
        status="completed",
        urls=urls,
    )
