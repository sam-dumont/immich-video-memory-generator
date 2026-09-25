"""The storyboard tab: the cut in the order it plays, rendered from the shared reader.

The data model and the reader live in `operations.storyboard`, because the
terminal prints the same record (`runs story`). This module only draws it.
"""

from __future__ import annotations

import base64

from nicegui import ui

from immich_memories.operations.storyboard import (
    PROJECTION_FILE,
    Shot,
    Storyboard,
    read_storyboard,
    storyboard_from_plan,
)
from immich_memories.ui.components import im_badge, im_card, im_section_header
from immich_memories.ui.pages.picture_decisions import (
    PictureHold,
    read_holds,
    render_picture_decision,
)
from immich_memories.ui.state import get_app_state

__all__ = [
    "PROJECTION_FILE",
    "Shot",
    "Storyboard",
    "read_storyboard",
    "render_storyboard",
    "storyboard_from_plan",
]

# The grid thumbnail first: a storyboard of forty shots must not cost forty previews. The
# session cache only holds what the pool loader fetched, which today is the preview, so
# that is the fallback until the media route (S3) serves sizes on demand.
_THUMBNAIL_SIZES = ("thumbnail", "preview")


def _thumbnail(asset_id: str) -> None:
    state = get_app_state()
    thumb = None
    if state.thumbnail_cache is not None:
        for size in _THUMBNAIL_SIZES:
            try:
                thumb = state.thumbnail_cache.get(asset_id, size)
            except Exception:  # WHY: a missing thumbnail must not take the storyboard down
                thumb = None
            if thumb:
                break
    if thumb:
        ui.image(f"data:image/jpeg;base64,{base64.b64encode(thumb).decode()}").classes(
            "rounded"
        ).style("width: 96px; aspect-ratio: 16/9; object-fit: cover")
    else:
        ui.element("div").classes("rounded").style(
            "width: 96px; aspect-ratio: 16/9; background: var(--im-bg-surface)"
        )


def _render_shot(shot: Shot, hold: PictureHold | None) -> None:
    with im_card() as card:
        card.classes("p-2 storyboard-shot")
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(shot.timecode).classes("text-xs font-mono w-10").style(
                "color: var(--im-text-secondary)"
            )
            _thumbnail(shot.asset_id)
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label(shot.day).classes(
                        "text-sm font-semibold storyboard-day" if shot.new_day else "text-sm"
                    ).style("color: var(--im-text)")
                    im_badge(shot.kind_label, variant="analysis" if shot.motion else "info")
                    ui.label(f"{shot.seconds:g} s").classes("text-xs").style(
                        "color: var(--im-text-secondary)"
                    )
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label(shot.story_title).classes("text-xs storyboard-story").style(
                        "color: var(--im-primary)"
                    )
                    if shot.moment:
                        ui.label(shot.moment).classes("text-xs").style(
                            "color: var(--im-text-secondary)"
                        )
                if shot.reason:
                    ui.label(shot.reason).classes("text-xs").style("color: var(--im-text)")
                render_picture_decision(shot.asset_id, hold)


def render_storyboard(
    board: Storyboard, note: str = "", warning: str | None = None, *, show_thesis: bool = True
) -> None:
    """The cut as it will play: chapters, days and pictures in order, each with the owner's
    clear-hold and never-use buttons."""
    im_section_header("The storyboard", icon="view_timeline")
    if show_thesis:
        ui.label(board.thesis or "The editor left no thesis for this cut.").classes(
            "text-lg"
        ).style("color: var(--im-text)")
    if note:
        ui.label(note).classes("text-sm mt-1").style("color: var(--im-text-secondary)")
    if warning:
        ui.label(warning).classes("text-sm").style("color: var(--im-warning)")
    im_section_header(board.summary_label, icon="movie")
    ui.label("Titles and transitions make up the rest of the film.").classes("text-xs mb-2").style(
        "color: var(--im-text-secondary)"
    )
    holds = read_holds(shot.asset_id for shot in board.shots)
    for shot in board.shots:
        if shot.chapter:
            ui.label(shot.chapter).classes("text-sm font-semibold mt-2 storyboard-chapter").style(
                "color: var(--im-text-secondary)"
            )
        _render_shot(shot, holds.get(shot.asset_id))
