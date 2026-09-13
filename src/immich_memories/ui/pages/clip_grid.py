"""Clip grid rendering for Step 2: Clip Review."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar

from nicegui import ui

from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.ui.components import im_badge
from immich_memories.ui.pages.step2_helpers import (
    format_duration,
    render_thumbnail,
)
from immich_memories.ui.state import get_app_state

CLIPS_PER_PAGE = 20

# Union type for items in the mixed grid
GridItem = VideoClipInfo | Asset
Item = TypeVar("Item")


def grid_item_date(item: GridItem) -> datetime:
    """Extract file_created_at from either a VideoClipInfo or an Asset."""
    if isinstance(item, VideoClipInfo):
        return item.asset.file_created_at
    return item.file_created_at


def _update_duration_summary(clips: list[VideoClipInfo], container: ui.element) -> None:
    """Update the duration summary display."""
    from immich_memories.ui.pages.step2_helpers import render_duration_summary

    state = get_app_state()

    selected_duration = 0.0
    for c in clips:
        if c.asset.id in state.selected_clip_ids:
            if c.asset.id in state.clip_segments:
                start, end = state.clip_segments[c.asset.id]
                selected_duration += end - start
            else:
                selected_duration += c.duration_seconds

    selected_count = len(state.selected_clip_ids)
    if state.include_photos:
        # WHY: photos ticked "Include" counted for nothing here, so 59 included
        # photos read as "Selected Clips: 1" beside the one video (#778).
        selected_count = len(state.selected_clip_ids | state.selected_photo_ids)
        selected_duration += len(state.selected_photo_ids) * state.photo_duration
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
    render_thumbnail(
        asset_id, classes="w-full rounded-lg", style="aspect-ratio: 16/9; object-fit: cover"
    )


def _render_clip_metadata(clip: VideoClipInfo) -> None:
    """Render date, duration, filename, and resolution metadata."""
    date_str = clip.asset.file_created_at.strftime("%b %d %H:%M")
    duration_str = format_duration(clip.duration_seconds) if clip.duration_seconds else "N/A"

    ui.label(date_str).classes("font-semibold text-sm").style("color: var(--im-text)")
    ui.label(f"⏱ {duration_str}").classes("text-xs").style("color: var(--im-text-secondary)")

    filename = clip.asset.original_file_name or "Unknown"
    if len(filename) > 20:
        filename = filename[:17] + "..."
    ui.label(filename).classes("text-xs truncate").style("color: var(--im-text-secondary)")

    if clip.width and clip.height:
        res_str = f"{clip.width}x{clip.height}"
        if clip.color_space:
            res_str += f" • {clip.color_space}"
        ui.label(res_str).classes("text-xs").style("color: var(--im-text-secondary)")


def _render_clip_card(
    clip: VideoClipInfo,
    state,
    all_clips: list[VideoClipInfo],
    summary_container: ui.element,
) -> None:
    """Render a single themed clip card."""
    is_selected = clip.asset.id in state.selected_clip_ids

    with (
        ui.card()
        .classes("p-2 rounded-xl")
        .style("background-color: var(--im-bg-elevated); border: 1px solid var(--im-border)")
    ):
        _render_clip_thumbnail(clip.asset.id)
        _render_clip_badges(_get_clip_badges(clip))
        _render_audio_categories(clip)
        _render_clip_metadata(clip)

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
        render_thumbnail(photo.id, classes="w-full h-full object-cover", style="")

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
        render_thumbnail(clip.asset.id, classes="w-full h-full object-cover", style="")

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


def _render_clip_grid(
    clips: list[VideoClipInfo],
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
            _render_clip_card(clip, state, all_clips, summary_container)


def _render_mixed_grid(
    items: list[GridItem],
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
                _render_clip_card(item, state, all_clips, summary_container)
            else:
                _render_photo_card(item, state, all_clips, summary_container)


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


def _render_paginated(
    items: Sequence[Item],
    render_page: Callable[[Sequence[Item]], None],
    page_size: int = CLIPS_PER_PAGE,
) -> None:
    """Render the first page now and the rest behind "Show more" buttons, a page at a time."""
    if len(items) <= page_size:
        render_page(items)
        return

    grid_container = ui.column().classes("w-full")
    with grid_container:
        render_page(items[:page_size])
    button_container = ui.row().classes("w-full justify-center mt-2")

    def load_more(remaining: Sequence[Item]) -> None:
        button_container.clear()
        with grid_container:
            render_page(remaining[:page_size])
        still_remaining = remaining[page_size:]
        if still_remaining:
            offer(still_remaining)

    def offer(remaining: Sequence[Item]) -> None:
        with button_container:
            ui.button(
                f"Show more ({len(remaining)} remaining)",
                on_click=lambda: load_more(remaining),
            ).props("outline")

    offer(items[page_size:])
