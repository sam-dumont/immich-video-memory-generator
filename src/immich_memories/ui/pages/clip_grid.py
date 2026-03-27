"""Clip grid rendering for Step 2: Clip Review."""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime

from nicegui import ui

from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.ui.components import im_badge
from immich_memories.ui.pages.step2_helpers import (
    format_duration,
    get_thumbnail,
)
from immich_memories.ui.state import get_app_state

CLIPS_PER_PAGE = 20

# Union type for items in the mixed grid
GridItem = VideoClipInfo | Asset


def grid_item_date(item: GridItem) -> datetime:
    """Extract file_created_at from either a VideoClipInfo or an Asset."""
    if isinstance(item, VideoClipInfo):
        return item.asset.file_created_at
    return item.file_created_at


def grid_item_id(item: GridItem) -> str:
    """Extract the asset ID from either a VideoClipInfo or an Asset."""
    if isinstance(item, VideoClipInfo):
        return item.asset.id
    return item.id


def clip_quality_score(c: VideoClipInfo) -> tuple[int, int, int, int]:
    """Score a clip for quality comparison. Higher is better."""
    res_score = c.width * c.height
    hdr_score = 1 if c.is_hdr else 0
    depth_score = c.bit_depth or 8
    bitrate_score = c.bitrate
    return (res_score, hdr_score, depth_score, bitrate_score)


def _detect_duplicates(
    clips: list[VideoClipInfo],
) -> tuple[set[str], set[str]]:
    """Detect duplicate clips and identify lower quality ones.

    Uses two strategies:
    1. Same-minute + same-duration: exact duplicate (different codec/quality)
    2. Thumbnail perceptual hash: catches messaging app copies (WhatsApp, etc.)
       that have different timestamps but visually identical content
    """
    duplicate_ids: set[str] = set()
    lower_quality_ids: set[str] = set()

    # Strategy 1: exact datetime+duration match
    clips_by_datetime = _group_clips_by_datetime(clips)
    for group in clips_by_datetime.values():
        if len(group) > 1:
            _mark_lower_quality(group, duplicate_ids, lower_quality_ids)

    # Strategy 2: thumbnail perceptual hash deduplication
    _detect_thumbnail_duplicates(clips, duplicate_ids, lower_quality_ids)

    return duplicate_ids, lower_quality_ids


def _mark_lower_quality(
    group: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
) -> None:
    """Within a group of duplicates, mark all but the best as lower quality."""
    sorted_group = sorted(group, key=clip_quality_score, reverse=True)
    best_clip = sorted_group[0]
    for c in group:
        duplicate_ids.add(c.asset.id)
        if c.asset.id != best_clip.asset.id:
            lower_quality_ids.add(c.asset.id)


def _detect_thumbnail_duplicates(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
) -> None:
    """Detect duplicates using thumbnail perceptual hashing.

    Catches messaging app copies (WhatsApp, Telegram, etc.) that have
    different timestamps/filenames but visually identical content.
    """
    state = get_app_state()
    thumbnail_cache = state.thumbnail_cache
    if thumbnail_cache is None:
        return

    try:
        from immich_memories.analysis.thumbnail_clustering import cluster_thumbnails
        from immich_memories.config import get_config

        clusters = cluster_thumbnails(
            clips=clips,
            thumbnail_cache=thumbnail_cache,
            duplicate_hash_threshold=get_config().analysis.duplicate_hash_threshold,
        )

        for cluster in clusters:
            if len(cluster.clip_ids) <= 1:
                continue
            # All members are duplicates; non-representatives are lower quality
            for clip_id in cluster.clip_ids:
                duplicate_ids.add(clip_id)
                if clip_id != cluster.representative_id:
                    lower_quality_ids.add(clip_id)

    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "Thumbnail dedup failed, falling back to datetime only", exc_info=True
        )


def _group_clips_by_datetime(clips: list[VideoClipInfo]) -> dict[str, list[VideoClipInfo]]:
    """Group clips by datetime and duration for duplicate detection."""
    clips_by_datetime: dict[str, list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        dt_key = clip.asset.file_created_at.strftime("%Y-%m-%d_%H:%M")
        dur_key = round(clip.duration_seconds or 0, 0)
        key = f"{dt_key}_{dur_key}"
        clips_by_datetime[key].append(clip)
    return clips_by_datetime


def _update_duration_summary(clips: list[VideoClipInfo], container: ui.element) -> None:
    """Update the duration summary display."""
    from immich_memories.ui.pages.step2_helpers import render_duration_summary

    state = get_app_state()
    avg_clip_sec = state.avg_clip_duration

    selected_duration = 0.0
    for c in clips:
        if c.asset.id in state.selected_clip_ids:
            if c.asset.id in state.clip_segments:
                start, end = state.clip_segments[c.asset.id]
                selected_duration += end - start
            else:
                selected_duration += min(c.duration_seconds or avg_clip_sec, avg_clip_sec)

    selected_count = len(state.selected_clip_ids)
    render_duration_summary(
        selected_duration, state.target_duration * 60, selected_count, container
    )


def _get_clip_badges(clip: VideoClipInfo) -> list[str]:
    """Build the list of badge labels for a clip card."""
    badges = []
    if clip.asset.is_live_photo:
        badges.append("Live")
    if clip.asset.is_favorite:
        badges.append("star")
    if clip.is_hdr:
        badges.append(clip.hdr_format or "HDR")
    if clip.width and clip.height:
        if clip.width >= 3840 or clip.height >= 2160:
            badges.append("4K")
        elif clip.width >= 1920 or clip.height >= 1080:
            badges.append("HD")
    return badges


# Colors for audio category tags (muted, distinct)
_CATEGORY_COLORS: dict[str, str] = {
    "laughter": "--im-error",
    "baby": "--im-error",
    "speech": "--im-text-secondary",
    "singing": "--im-analysis",
    "music": "--im-analysis",
    "engine": "--im-warning",
    "nature": "--im-success",
    "crowd": "--im-warning",
    "animals": "--im-warning-text",
}


def _render_audio_categories(clip: VideoClipInfo) -> None:
    """Render detected audio category tags on a clip card."""
    if not clip.audio_categories:
        return
    with ui.row().classes("gap-1 flex-wrap mt-1"):
        for cat in clip.audio_categories:
            css_var = _CATEGORY_COLORS.get(cat, "--im-text-secondary")
            ui.badge(cat).classes("text-xs").style(
                f"background: color-mix(in srgb, var({css_var}) 20%, var(--im-bg-elevated)); "
                f"color: var({css_var})"
            )


def _render_clip_badges(badges: list[str]) -> None:
    """Render badge icons/labels for a clip card."""
    if badges:
        with ui.row().classes("gap-1 flex-wrap"):
            for badge in badges:
                if badge == "star":
                    ui.icon("star").classes("text-xs").style("color: var(--im-warning)")
                elif badge == "Live":
                    im_badge(badge, variant="analysis")
                else:
                    im_badge(badge, variant="info")


def _render_clip_thumbnail(asset_id: str) -> None:
    """Render the thumbnail image or placeholder for a clip card."""
    thumb = get_thumbnail(asset_id)
    if thumb:
        b64 = base64.b64encode(thumb).decode()
        ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full rounded-lg").style(
            "aspect-ratio: 16/9; object-fit: cover"
        )
    else:
        ui.element("div").classes("w-full rounded-lg").style(
            "aspect-ratio: 16/9; background: var(--im-bg-surface)"
        )


def _render_clip_metadata(clip: VideoClipInfo) -> None:
    """Render date, duration, filename, and resolution metadata."""
    date_str = clip.asset.file_created_at.strftime("%b %d %H:%M")
    duration_str = format_duration(clip.duration_seconds) if clip.duration_seconds else "N/A"

    ui.label(date_str).classes("font-semibold text-sm").style("color: var(--im-text)")
    ui.label(f"\u23f1 {duration_str}").classes("text-xs").style("color: var(--im-text-secondary)")

    filename = clip.asset.original_file_name or "Unknown"
    if len(filename) > 20:
        filename = filename[:17] + "..."
    ui.label(filename).classes("text-xs truncate").style("color: var(--im-text-secondary)")

    if clip.width and clip.height:
        res_str = f"{clip.width}x{clip.height}"
        if clip.color_space:
            res_str += f" \u2022 {clip.color_space}"
        ui.label(res_str).classes("text-xs").style("color: var(--im-text-secondary)")


def _render_duplicate_indicator(is_duplicate: bool, is_best: bool) -> None:
    """Render the duplicate/best quality indicator label."""
    if not is_duplicate:
        return
    if is_best:
        ui.label("Best").classes("text-xs font-semibold").style("color: var(--im-success)")
    else:
        ui.label("Duplicate").classes("text-xs").style("color: var(--im-warning)")


def _render_clip_card(
    clip: VideoClipInfo,
    state,
    all_clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render a single themed clip card."""
    is_selected = clip.asset.id in state.selected_clip_ids
    is_duplicate = clip.asset.id in duplicate_ids
    is_best = is_duplicate and clip.asset.id not in lower_quality_ids

    with (
        ui.card()
        .classes("p-2 rounded-xl")
        .style("background-color: var(--im-bg-elevated); border: 1px solid var(--im-border)")
    ):
        _render_clip_thumbnail(clip.asset.id)
        _render_clip_badges(_get_clip_badges(clip))
        _render_audio_categories(clip)
        _render_clip_metadata(clip)
        _render_duplicate_indicator(is_duplicate, is_best)

        # Selection checkbox
        def make_toggle_handler(asset_id: str):
            def toggle(e):
                value = e.value if hasattr(e, "value") else e
                if value:
                    state.selected_clip_ids.add(asset_id)
                else:
                    state.selected_clip_ids.discard(asset_id)
                _update_duration_summary(all_clips, summary_container)

            return toggle

        checkbox = ui.checkbox("Include", value=is_selected)
        checkbox.on_value_change(make_toggle_handler(clip.asset.id))


def _render_photo_card(
    photo: Asset,
    state,
    all_clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render a single photo card in the list view."""
    is_selected = photo.id in state.selected_photo_ids

    with (
        ui.card()
        .classes("p-2 rounded-xl")
        .style("background-color: var(--im-bg-elevated); border: 1px solid var(--im-border)")
    ):
        _render_clip_thumbnail(photo.id)

        with ui.row().classes("gap-1 flex-wrap"):
            im_badge("Photo", variant="analysis")
            if photo.is_favorite:
                ui.icon("star").classes("text-xs").style("color: var(--im-warning)")

        date_str = photo.file_created_at.strftime("%b %d %H:%M")
        ui.label(date_str).classes("font-semibold text-sm").style("color: var(--im-text)")

        filename = photo.original_file_name or "Unknown"
        if len(filename) > 20:
            filename = filename[:17] + "..."
        ui.label(filename).classes("text-xs truncate").style("color: var(--im-text-secondary)")

        def make_photo_toggle(photo_id: str):
            def toggle(e):
                value = e.value if hasattr(e, "value") else e
                if value:
                    state.selected_photo_ids.add(photo_id)
                else:
                    state.selected_photo_ids.discard(photo_id)
                _update_duration_summary(all_clips, summary_container)

            return toggle

        checkbox = ui.checkbox("Include", value=is_selected)
        checkbox.on_value_change(make_photo_toggle(photo.id))


def _render_compact_photo_thumbnail(
    photo: Asset,
    state,
    all_clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render a single compact photo thumbnail cell with selection overlay."""
    is_selected = photo.id in state.selected_photo_ids
    tooltip = f"Photo | {photo.file_created_at.strftime('%b %d, %Y %H:%M')}"

    def make_click_handler(photo_id: str):
        def toggle():
            if photo_id in state.selected_photo_ids:
                state.selected_photo_ids.discard(photo_id)
            else:
                state.selected_photo_ids.add(photo_id)
            _update_duration_summary(all_clips, summary_container)
            ui.navigate.to("/step2")

        return toggle

    border = "2px solid var(--im-primary)" if is_selected else "1px solid var(--im-border)"
    with (
        ui.element("div")
        .classes("relative cursor-pointer aspect-video rounded-lg overflow-hidden")
        .style(f"border: {border}")
        .tooltip(tooltip)
        .on("click", make_click_handler(photo.id))
    ):
        thumb = get_thumbnail(photo.id)
        if thumb:
            b64 = base64.b64encode(thumb).decode()
            ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-full object-cover")
        else:
            ui.element("div").classes("w-full h-full flex items-center justify-center").style(
                "background-color: var(--im-bg-surface)"
            )

        # Camera icon badge in top-left to distinguish from videos
        ui.icon("photo_camera", color="white", size="16px").classes("absolute top-1 left-1").style(
            "filter: drop-shadow(0 1px 2px rgba(0,0,0,0.5))"
        )

        if is_selected:
            ui.element("div").classes("absolute inset-0").style(
                "background: rgba(66, 80, 175, 0.25)"
            )
            ui.icon("check_circle", color="white", size="20px").classes(
                "absolute top-1 right-1"
            ).style("filter: drop-shadow(0 1px 2px rgba(0,0,0,0.5))")


def _build_clip_tooltip(clip: VideoClipInfo) -> str:
    """Build hover tooltip text for compact grid view."""
    parts = []
    if clip.duration_seconds:
        parts.append(format_duration(clip.duration_seconds))
    parts.append(clip.asset.file_created_at.strftime("%b %d, %Y %H:%M"))
    if clip.asset.people:
        parts.append(clip.asset.people[0].name)
    return " | ".join(parts)


def _render_compact_thumbnail(
    clip: VideoClipInfo,
    state,
    all_clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render a single compact thumbnail cell with selection overlay."""
    is_selected = clip.asset.id in state.selected_clip_ids
    tooltip = _build_clip_tooltip(clip)

    def make_click_handler(asset_id: str):
        def toggle():
            if asset_id in state.selected_clip_ids:
                state.selected_clip_ids.discard(asset_id)
            else:
                state.selected_clip_ids.add(asset_id)
            _update_duration_summary(all_clips, summary_container)
            ui.navigate.to("/step2")

        return toggle

    border = "2px solid var(--im-primary)" if is_selected else "1px solid var(--im-border)"
    with (
        ui.element("div")
        .classes("relative cursor-pointer aspect-video rounded-lg overflow-hidden")
        .style(f"border: {border}")
        .tooltip(tooltip)
        .on("click", make_click_handler(clip.asset.id))
    ):
        thumb = get_thumbnail(clip.asset.id)
        if thumb:
            b64 = base64.b64encode(thumb).decode()
            ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-full object-cover")
        else:
            ui.element("div").classes("w-full h-full flex items-center justify-center").style(
                "background-color: var(--im-bg-surface)"
            )

        if is_selected:
            # Selection overlay: semi-transparent tint + check icon
            ui.element("div").classes("absolute inset-0").style(
                "background: rgba(66, 80, 175, 0.25)"
            )
            ui.icon("check_circle", color="white", size="20px").classes(
                "absolute top-1 right-1"
            ).style("filter: drop-shadow(0 1px 2px rgba(0,0,0,0.5))")


def _render_compact_grid(
    clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render a responsive compact thumbnail grid."""
    state = get_app_state()
    all_clips = state.clips

    with (
        ui.element("div")
        .classes("w-full grid gap-2")
        .style("grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))")
    ):
        for clip in clips:
            _render_compact_thumbnail(clip, state, all_clips, summary_container)


def _render_compact_grid_paginated(
    clips: list[VideoClipInfo],
    summary_container: ui.element,
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render a paginated compact thumbnail grid."""
    if len(clips) <= page_size:
        _render_compact_grid(clips, summary_container)
        return

    grid_container = ui.column().classes("w-full")
    with grid_container:
        _render_compact_grid(clips[:page_size], summary_container)

    remaining = clips[page_size:]
    if remaining:
        btn_container = ui.row().classes("w-full justify-center mt-2")
        with btn_container:

            def load_more(
                remaining_clips=remaining,
                parent=grid_container,
                btn_ctr=btn_container,
            ):
                btn_ctr.clear()
                with parent:
                    _render_compact_grid(remaining_clips[:page_size], summary_container)
                still_remaining = remaining_clips[page_size:]
                if still_remaining:
                    with btn_ctr:
                        ui.button(
                            f"Show more ({len(still_remaining)} remaining)",
                            on_click=lambda sr=still_remaining: load_more(sr, parent, btn_ctr),
                        ).props("outline")

            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=load_more,
            ).props("outline")


def _render_clip_grid(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render a responsive grid of clip cards."""
    state = get_app_state()
    all_clips = state.clips

    with (
        ui.element("div")
        .classes("w-full grid gap-3")
        .style("grid-template-columns: repeat(auto-fill, minmax(200px, 1fr))")
    ):
        for clip in clips:
            _render_clip_card(
                clip, state, all_clips, duplicate_ids, lower_quality_ids, summary_container
            )


def _render_clip_grid_paginated(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render a paginated grid of clip cards (loads in batches)."""
    if len(clips) <= page_size:
        _render_clip_grid(clips, duplicate_ids, lower_quality_ids, summary_container)
        return

    grid_container = ui.column().classes("w-full")
    with grid_container:
        _render_clip_grid(clips[:page_size], duplicate_ids, lower_quality_ids, summary_container)

    remaining = clips[page_size:]
    if remaining:
        btn_container = ui.row().classes("w-full justify-center mt-2")
        with btn_container:

            def load_more(
                remaining_clips=remaining,
                parent=grid_container,
                btn_ctr=btn_container,
            ):
                btn_ctr.clear()
                with parent:
                    _render_clip_grid(
                        remaining_clips[:page_size],
                        duplicate_ids,
                        lower_quality_ids,
                        summary_container,
                    )
                still_remaining = remaining_clips[page_size:]
                if still_remaining:
                    with btn_ctr:
                        ui.button(
                            f"Show more ({len(still_remaining)} remaining)",
                            on_click=lambda sr=still_remaining: load_more(sr, parent, btn_ctr),
                        ).props("outline")

            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=load_more,
            ).props("outline")


def _render_mixed_grid(
    items: list[GridItem],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render a mixed grid of video clips and photos, sorted chronologically."""
    state = get_app_state()
    all_clips = state.clips

    with ui.element("div").classes(
        "grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4"
    ):
        for item in items:
            if isinstance(item, VideoClipInfo):
                _render_clip_card(
                    item, state, all_clips, duplicate_ids, lower_quality_ids, summary_container
                )
            else:
                _render_photo_card(item, state, all_clips, summary_container)


def _render_mixed_grid_paginated(
    items: list[GridItem],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render a paginated mixed grid of video clips and photos."""
    if len(items) <= page_size:
        _render_mixed_grid(items, duplicate_ids, lower_quality_ids, summary_container)
        return

    grid_container = ui.column().classes("w-full")
    with grid_container:
        _render_mixed_grid(items[:page_size], duplicate_ids, lower_quality_ids, summary_container)

    remaining = items[page_size:]
    if remaining:
        btn_container = ui.row().classes("w-full justify-center mt-2")
        with btn_container:

            def load_more(
                remaining_items=remaining,
                parent=grid_container,
                btn_ctr=btn_container,
            ):
                btn_ctr.clear()
                with parent:
                    _render_mixed_grid(
                        remaining_items[:page_size],
                        duplicate_ids,
                        lower_quality_ids,
                        summary_container,
                    )
                still_remaining = remaining_items[page_size:]
                if still_remaining:
                    with btn_ctr:
                        ui.button(
                            f"Show more ({len(still_remaining)} remaining)",
                            on_click=lambda sr=still_remaining: load_more(sr, parent, btn_ctr),
                        ).props("outline")

            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=load_more,
            ).props("outline")


def _render_compact_mixed_grid(
    items: list[GridItem],
    summary_container: ui.element,
) -> None:
    """Render a compact mixed grid of video clips and photos."""
    state = get_app_state()
    all_clips = state.clips

    with ui.element("div").classes("grid grid-cols-4 sm:grid-cols-5 lg:grid-cols-6 gap-2"):
        for item in items:
            if isinstance(item, VideoClipInfo):
                _render_compact_thumbnail(item, state, all_clips, summary_container)
            else:
                _render_compact_photo_thumbnail(item, state, all_clips, summary_container)


def _render_compact_mixed_grid_paginated(
    items: list[GridItem],
    summary_container: ui.element,
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render a paginated compact mixed grid."""
    if len(items) <= page_size:
        _render_compact_mixed_grid(items, summary_container)
        return

    grid_container = ui.column().classes("w-full")
    with grid_container:
        _render_compact_mixed_grid(items[:page_size], summary_container)

    remaining = items[page_size:]
    if remaining:
        btn_container = ui.row().classes("w-full justify-center mt-2")
        with btn_container:

            def load_more(
                remaining_items=remaining,
                parent=grid_container,
                btn_ctr=btn_container,
            ):
                btn_ctr.clear()
                with parent:
                    _render_compact_mixed_grid(remaining_items[:page_size], summary_container)
                still_remaining = remaining_items[page_size:]
                if still_remaining:
                    with btn_ctr:
                        ui.button(
                            f"Show more ({len(still_remaining)} remaining)",
                            on_click=lambda sr=still_remaining: load_more(sr, parent, btn_ctr),
                        ).props("outline")

            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=load_more,
            ).props("outline")
