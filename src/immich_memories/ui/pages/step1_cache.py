"""Cache management section for Step 1 configuration page."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nicegui import run, ui

logger = logging.getLogger(__name__)

# Preview cache path (not managed by a dedicated class)
_PREVIEW_CACHE_DIR = Path("~/.immich-memories/cache/preview-cache").expanduser()


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_preview_cache_stats() -> dict:
    """Get preview cache stats (no dedicated cache class)."""
    if not _PREVIEW_CACHE_DIR.exists():
        return {"file_count": 0, "total_size_bytes": 0}
    files = list(_PREVIEW_CACHE_DIR.glob("*.mp4"))
    total = sum(f.stat().st_size for f in files)
    return {"file_count": len(files), "total_size_bytes": total}


def _clear_preview_cache() -> int:
    """Clear preview cache directory."""
    if not _PREVIEW_CACHE_DIR.exists():
        return 0
    files = list(_PREVIEW_CACHE_DIR.glob("*.mp4"))
    count = len(files)
    shutil.rmtree(_PREVIEW_CACHE_DIR)
    return count


def _render_cache_row(
    label: str,
    icon: str,
    stat_text: str,
    on_clear,
) -> None:
    """Render a single cache row with stats and clear button."""
    with ui.row().classes("w-full items-center justify-between py-2"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon).classes("text-gray-500")
            ui.label(label).classes("font-medium")
        ui.label(stat_text).classes("text-sm text-gray-500")
        ui.button("Clear", on_click=on_clear, icon="delete_outline").props(
            "flat size=sm color=negative"
        )


def render_cache_management() -> None:
    """Render cache management section with stats and clear buttons."""
    ui.separator().classes("my-6")

    with ui.expansion("Cache Management", icon="storage").classes("w-full").props("dense"):
        stats_container = ui.column().classes("w-full gap-0")

        async def refresh_stats() -> None:
            """Load cache stats and render rows."""
            stats_container.clear()

            # Gather stats in background thread (DB + filesystem ops)
            def _gather():
                from immich_memories.cache import (
                    ThumbnailCache,
                    VideoAnalysisCache,
                    VideoDownloadCache,
                )

                analysis = VideoAnalysisCache()
                video = VideoDownloadCache()
                thumbnail = ThumbnailCache()
                return {
                    "analysis": analysis.get_stats(),
                    "video": video.get_stats(),
                    "thumbnail": thumbnail.get_stats(),
                    "preview": _get_preview_cache_stats(),
                }

            all_stats = await run.io_bound(_gather)

            with stats_container:
                # Analysis cache
                a = all_stats["analysis"]
                a_text = f"{a['total_videos']} videos, {_format_size(a['database_size_bytes'])}"

                async def clear_analysis():
                    from immich_memories.cache import VideoAnalysisCache

                    count = await run.io_bound(VideoAnalysisCache().clear_all)
                    ui.notify(f"Cleared {count} analysis entries", type="positive")
                    await refresh_stats()

                _render_cache_row("Analysis cache", "analytics", a_text, clear_analysis)

                # Video download cache
                v = all_stats["video"]
                v_text = (
                    f"{v['file_count']} files, "
                    f"{_format_size(v['total_size_bytes'])} / {v['max_size_gb']:.0f} GB"
                )

                async def clear_video():
                    from immich_memories.cache import VideoDownloadCache

                    count = await run.io_bound(VideoDownloadCache().clear)
                    ui.notify(f"Cleared {count} cached videos", type="positive")
                    await refresh_stats()

                _render_cache_row("Video cache", "movie", v_text, clear_video)

                # Thumbnail cache
                t = all_stats["thumbnail"]
                t_text = (
                    f"{t['file_count']} files, "
                    f"{_format_size(t['total_size_bytes'])} / {t['max_size_mb']:.0f} MB"
                )

                async def clear_thumbnail():
                    from immich_memories.cache import ThumbnailCache

                    count = await run.io_bound(ThumbnailCache().clear)
                    ui.notify(f"Cleared {count} cached thumbnails", type="positive")
                    await refresh_stats()

                _render_cache_row("Thumbnail cache", "image", t_text, clear_thumbnail)

                # Preview cache
                p = all_stats["preview"]
                p_text = f"{p['file_count']} files, {_format_size(p['total_size_bytes'])}"

                async def clear_preview():
                    count = await run.io_bound(_clear_preview_cache)
                    ui.notify(f"Cleared {count} preview files", type="positive")
                    await refresh_stats()

                _render_cache_row("Preview cache", "play_circle", p_text, clear_preview)

                # Clear all button
                ui.separator().classes("my-2")

                async def clear_all():
                    from immich_memories.cache import (
                        ThumbnailCache,
                        VideoAnalysisCache,
                        VideoDownloadCache,
                    )

                    def _do_clear_all():
                        a = VideoAnalysisCache().clear_all()
                        v = VideoDownloadCache().clear()
                        t = ThumbnailCache().clear()
                        p = _clear_preview_cache()
                        return a + v + t + p

                    total = await run.io_bound(_do_clear_all)
                    ui.notify(f"Cleared all caches ({total} items)", type="positive")
                    await refresh_stats()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Clear all caches", on_click=clear_all, icon="delete_sweep").props(
                        "outline color=negative"
                    )

        # Load stats when expansion is opened
        ui.timer(0.1, refresh_stats, once=True)
