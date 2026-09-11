"""What the cut produced: the thesis, its stories, and the pictures each was granted."""

from __future__ import annotations

import base64
from datetime import datetime

from nicegui import ui

from immich_memories.ui.components import im_badge, im_card, im_section_header
from immich_memories.ui.pages.memory_story_data import CarrierView, StoryEntry, StoryView
from immich_memories.ui.pages.step2_helpers import get_thumbnail

_WEIGHT_VARIANT = {"dominant": "success", "major": "success", "minor": "info"}
_STANDING_VARIANT = {"remarkable": "success", "maybe": "warning"}


def _when(taken: str) -> str:
    try:
        return datetime.fromisoformat(taken).strftime("%b %d, %H:%M")
    except ValueError:
        return taken


def _render_thumbnail(asset_id: str) -> None:
    thumb = get_thumbnail(asset_id)
    if thumb:
        encoded = base64.b64encode(thumb).decode()
        ui.image(f"data:image/jpeg;base64,{encoded}").classes("rounded").style(
            "width: 128px; aspect-ratio: 16/9; object-fit: cover"
        )
    else:
        ui.element("div").classes("rounded").style(
            "width: 128px; aspect-ratio: 16/9; background: var(--im-bg-surface)"
        )


def _render_carrier(carrier: CarrierView) -> None:
    with ui.row().classes("w-full items-start gap-3"):
        _render_thumbnail(carrier.asset_id)
        with ui.column().classes("gap-1 flex-1"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                im_badge(carrier.render_label, variant="analysis" if carrier.motion else "info")
                ui.label(f"{carrier.seconds:g} s").classes("text-xs").style(
                    "color: var(--im-text-secondary)"
                )
                ui.label(_when(carrier.taken)).classes("text-xs").style(
                    "color: var(--im-text-secondary)"
                )
                if carrier.standing:
                    im_badge(
                        carrier.standing, variant=_STANDING_VARIANT.get(carrier.standing, "info")
                    )
            if carrier.reason:
                ui.label(carrier.reason).classes("text-sm").style("color: var(--im-text)")


def _render_story(story: StoryEntry) -> None:
    with im_card() as card:
        card.classes("p-4")
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.label(story.title).classes("text-base font-semibold").style("color: var(--im-text)")
            if story.weight:
                im_badge(story.weight, variant=_WEIGHT_VARIANT.get(story.weight, "warning"))
            ui.label(f"{story.granted} granted").classes("text-xs").style(
                "color: var(--im-text-secondary)"
            )
            if story.day:
                ui.label(story.day).classes("text-xs").style("color: var(--im-text-secondary)")
        if story.purpose:
            ui.label(story.purpose).classes("text-sm italic mb-2").style(
                "color: var(--im-text-secondary)"
            )
        for carrier in story.carriers:
            _render_carrier(carrier)


def render_story(view: StoryView, warning: str | None = None) -> None:
    """The thesis, one duration line, then every story with its carriers."""
    im_section_header("The story", icon="auto_stories")
    ui.label(view.thesis or "The editor left no thesis for this cut.").classes("text-lg").style(
        "color: var(--im-text)"
    )
    if view.duration is not None:
        ui.label(view.duration.line).classes("text-sm mt-1").style(
            "color: var(--im-text-secondary)"
        )
    if warning:
        ui.label(warning).classes("text-sm").style("color: var(--im-warning)")
    im_section_header(f"{len(view.stories)} stories, {view.carrier_count} pictures", icon="movie")
    for story in view.stories:
        _render_story(story)
