"""Media cache controls for the Settings page."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nicegui import ui

from immich_memories.ui.nicegui_compat import io_bound_result

logger = logging.getLogger(__name__)


def _preview_cache_dir() -> Path:
    """Use the same configured directory as the clip preview writer."""
    from immich_memories.config import get_config

    return get_config().cache.cache_path / "preview-cache"


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
    preview_dir = _preview_cache_dir()
    if not preview_dir.exists():
        return {"file_count": 0, "total_size_bytes": 0}
    files = list(preview_dir.glob("*.mp4"))
    total = sum(f.stat().st_size for f in files)
    return {"file_count": len(files), "total_size_bytes": total}


def _clear_preview_cache() -> int:
    """Clear preview cache directory."""
    preview_dir = _preview_cache_dir()
    if not preview_dir.exists():
        return 0
    files = list(preview_dir.glob("*.mp4"))
    count = len(files)
    shutil.rmtree(preview_dir)
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
            ui.icon(icon).style("color: var(--im-text-secondary)")
            ui.label(label).classes("font-medium")
        ui.label(stat_text).classes("text-sm").style("color: var(--im-text-secondary)")
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
                    VideoDownloadCache,
                )
                from immich_memories.config import get_config

                _cfg = get_config()
                video = VideoDownloadCache(cache_dir=_cfg.cache.video_cache_path)
                thumbnail = ThumbnailCache(
                    cache_dir=_cfg.cache.cache_path / "thumbnails",
                    max_size_mb=_cfg.cache.thumbnail_cache_max_size_mb,
                )
                return {
                    "video": video.get_stats(),
                    "thumbnail": thumbnail.get_stats(),
                    "preview": _get_preview_cache_stats(),
                }

            all_stats = await io_bound_result(_gather)

            with stats_container:
                # Video download cache
                v = all_stats["video"]
                v_text = (
                    f"{v['file_count']} files, "
                    f"{_format_size(v['total_size_bytes'])} / {v['max_size_gb']:.0f} GB"
                )

                async def clear_video():
                    from immich_memories.cache import VideoDownloadCache
                    from immich_memories.config import get_config

                    _cfg = get_config()
                    count = await io_bound_result(
                        VideoDownloadCache(cache_dir=_cfg.cache.video_cache_path).clear
                    )
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
                    from immich_memories.config import get_config

                    _cfg = get_config()
                    count = await io_bound_result(
                        ThumbnailCache(
                            cache_dir=_cfg.cache.cache_path / "thumbnails",
                            max_size_mb=_cfg.cache.thumbnail_cache_max_size_mb,
                        ).clear
                    )
                    ui.notify(f"Cleared {count} cached thumbnails", type="positive")
                    await refresh_stats()

                _render_cache_row("Thumbnail cache", "image", t_text, clear_thumbnail)

                # Preview cache
                p = all_stats["preview"]
                p_text = f"{p['file_count']} files, {_format_size(p['total_size_bytes'])}"

                async def clear_preview():
                    count = await io_bound_result(_clear_preview_cache)
                    ui.notify(f"Cleared {count} preview files", type="positive")
                    await refresh_stats()

                _render_cache_row("Preview cache", "play_circle", p_text, clear_preview)

                ui.separator().classes("my-2")
                ui.label("Annotation records and run history are kept.").classes("text-sm")

                async def clear_all():
                    from immich_memories.cache import (
                        ThumbnailCache,
                        VideoDownloadCache,
                    )

                    def _do_clear_all():
                        from immich_memories.config import get_config

                        _cfg = get_config()
                        v = VideoDownloadCache(cache_dir=_cfg.cache.video_cache_path).clear()
                        t = ThumbnailCache(
                            cache_dir=_cfg.cache.cache_path / "thumbnails",
                            max_size_mb=_cfg.cache.thumbnail_cache_max_size_mb,
                        ).clear()
                        p = _clear_preview_cache()
                        return v + t + p

                    total = await io_bound_result(_do_clear_all)
                    ui.notify(f"Cleared media caches ({total} items)", type="positive")
                    await refresh_stats()

                with ui.row().classes("w-full justify-end"):
                    ui.button("Clear media caches", on_click=clear_all, icon="delete_sweep").props(
                        "outline color=negative"
                    )

        # Load stats when expansion is opened
        ui.timer(0.1, refresh_stats, once=True)
