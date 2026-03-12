"""Clip grid rendering for Step 2: Clip Review."""

from __future__ import annotations

import base64
from collections import defaultdict

from nicegui import ui

from immich_memories.api.models import VideoClipInfo
from immich_memories.ui.pages.step2_helpers import (
    format_duration,
    get_thumbnail,
)
from immich_memories.ui.state import get_app_state

CLIPS_PER_PAGE = 20


def clip_quality_score(c: VideoClipInfo) -> tuple[int, int, int, int]:
    """Score a clip for quality comparison. Higher is better."""
    res_score = (c.width or 0) * (c.height or 0)
    hdr_score = 1 if c.is_hdr else 0
    depth_score = c.bit_depth or 8
    bitrate_score = c.bitrate or 0
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
    for _key, group in clips_by_datetime.items():
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

        clusters = cluster_thumbnails(
            clips=clips,
            thumbnail_cache=thumbnail_cache,
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
    "laughter": "pink-4",
    "baby": "pink-3",
    "speech": "blue-grey-4",
    "singing": "purple-4",
    "music": "deep-purple-4",
    "engine": "orange-6",
    "nature": "green-5",
    "crowd": "amber-6",
    "animals": "brown-4",
}


def _render_audio_categories(clip: VideoClipInfo) -> None:
    """Render detected audio category tags on a clip card."""
    if not clip.audio_categories:
        return
    with ui.row().classes("gap-1 flex-wrap mt-1"):
        for cat in clip.audio_categories:
            color = _CATEGORY_COLORS.get(cat, "grey-5")
            ui.badge(cat, color=color).classes("text-xs")


def _render_clip_badges(badges: list[str]) -> None:
    """Render badge icons/labels for a clip card."""
    if badges:
        with ui.row().classes("gap-1 flex-wrap"):
            for badge in badges:
                if badge == "star":
                    ui.icon("star", color="yellow").classes("text-xs")
                else:
                    ui.badge(badge, color="blue").classes("text-xs")


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

    date_str = clip.asset.file_created_at.strftime("%b %d %H:%M")
    duration_str = format_duration(clip.duration_seconds) if clip.duration_seconds else "N/A"

    with (
        ui.card()
        .classes("p-2 rounded-xl")
        .style("background-color: var(--im-bg-elevated); border: 1px solid var(--im-border)")
    ):
        # Thumbnail
        thumb = get_thumbnail(clip.asset.id)
        if thumb:
            b64 = base64.b64encode(thumb).decode()
            ui.image(f"data:image/jpeg;base64,{b64}").classes("w-full h-24 object-cover rounded-lg")
        else:
            ui.element("div").classes(
                "w-full h-24 rounded-lg flex items-center justify-center"
            ).style("background-color: var(--im-bg-surface)")

        # Badges
        _render_clip_badges(_get_clip_badges(clip))

        # Audio category tags
        _render_audio_categories(clip)

        # Date and duration
        ui.label(date_str).classes("font-semibold text-sm").style("color: var(--im-text)")
        ui.label(f"\u23f1 {duration_str}").classes("text-xs").style(
            "color: var(--im-text-secondary)"
        )

        # Filename
        filename = clip.asset.original_file_name or "Unknown"
        if len(filename) > 20:
            filename = filename[:17] + "..."
        ui.label(filename).classes("text-xs truncate").style("color: var(--im-text-secondary)")

        # Resolution
        if clip.width and clip.height:
            res_str = f"{clip.width}x{clip.height}"
            if clip.color_space:
                res_str += f" \u2022 {clip.color_space}"
            ui.label(res_str).classes("text-xs").style("color: var(--im-text-secondary)")

        # Duplicate indicator
        if is_duplicate:
            if is_best:
                ui.label("Best").classes("text-xs font-semibold").style("color: var(--im-success)")
            else:
                ui.label("Duplicate").classes("text-xs").style("color: var(--im-warning)")

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


def _render_clip_grid(
    clips: list[VideoClipInfo],
    duplicate_ids: set[str],
    lower_quality_ids: set[str],
    summary_container: ui.element,
) -> None:
    """Render a responsive grid of clip cards."""
    state = get_app_state()
    all_clips = state.clips

    with ui.element("div").classes(
        "grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4"
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
