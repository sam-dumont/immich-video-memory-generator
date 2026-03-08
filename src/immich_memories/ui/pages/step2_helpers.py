"""Shared helpers for Step 2: Clip Review page."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import ui

from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def get_thumbnail(asset_id: str) -> bytes | None:
    """Get thumbnail from cache on-demand."""
    state = get_app_state()
    if state.thumbnail_cache is None:
        return None
    try:
        return state.thumbnail_cache.get(asset_id, "preview")
    except Exception:
        return None


def _get_preview_path(asset_id: str) -> Path | None:
    """Get or create an H.264 480p preview for a clip (Chrome/Windows compatible).

    Returns the path to the preview file, or None if the source isn't cached.
    """
    import subprocess

    from immich_memories.config import get_config

    config = get_config()
    preview_dir = config.cache.cache_path / "preview-cache"
    preview_dir.mkdir(parents=True, exist_ok=True)

    preview_path = preview_dir / f"{asset_id}.mp4"
    if preview_path.exists():
        return preview_path

    # Find the source video in the video cache
    # Cache uses two-level dirs: {cache_dir}/{id[:2]}/{id}{ext}
    # Files can be {id}.MOV (original) or {id}_480p.MOV (analysis downscale)
    video_cache_dir = config.cache.video_cache_path
    if not video_cache_dir.exists():
        return None

    subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
    sub_path = video_cache_dir / subdir
    if not sub_path.exists():
        return None

    # Prefer the _480p downscale (already small, faster to transcode)
    source = None
    for pattern in [f"{asset_id}_480p.*", f"{asset_id}.*"]:
        matches = list(sub_path.glob(pattern))
        if matches:
            source = matches[0]
            break

    if source is None:
        return None

    # Transcode to H.264 480p — fast, small, plays everywhere
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=-2:480",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(preview_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and preview_path.exists():
            return preview_path
        logger.warning(f"Preview transcode failed for {asset_id}: {result.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"Preview transcode error for {asset_id}: {e}")

    return None


def render_duration_summary(
    total_duration: float,
    target_duration: float,
    clip_count: int,
    container: ui.element,
) -> None:
    """Render duration summary."""
    container.clear()
    with container, ui.row().classes("w-full gap-8"):
        with ui.column().classes("items-center"):
            ui.label("Selected Clips").classes("text-sm text-gray-500")
            ui.label(str(clip_count)).classes("text-2xl font-bold")
        with ui.column().classes("items-center"):
            ui.label("Total Duration").classes("text-sm text-gray-500")
            ui.label(format_duration(total_duration)).classes("text-2xl font-bold")
