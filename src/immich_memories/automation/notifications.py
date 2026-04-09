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

    # Extract thumbnail from output video if available
    attach = _extract_thumbnail(output_path) if output_path and status == "completed" else None

    try:
        kwargs: dict = {"title": title, "body": body}
        if attach:
            kwargs["attach"] = attach
        result = bool(apobj.notify(**kwargs))
    except (OSError, RuntimeError) as e:
        logger.exception("Notification delivery error: %s", e)
        return False
    finally:
        if attach:
            _cleanup_thumbnail(attach)

    if result:
        logger.info("Notification sent: %s", title)
    else:
        logger.warning("Notification delivery failed: %s", title)
    return result


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


def send_test_notification(urls: list[str]) -> bool:
    """Send a test notification to verify Apprise URL configuration."""
    return notify_job_complete(
        memory_type="test",
        status="completed",
        urls=urls,
    )
