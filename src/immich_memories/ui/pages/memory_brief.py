"""The brief: what the memory is about, how long it runs, and the Cut that starts it.

Reads like `immich-memories generate`: one memory type, that type's own
parameters, one duration, one button. Everything else lives under Advanced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from immich_memories.memory_types.registry import OFFERED_MEMORY_TYPES, MemoryType
from immich_memories.ui.components import (
    im_button,
    im_card,
    im_info_card,
    im_section_header,
    im_separator,
)
from immich_memories.ui.pages.memory_duration import render_duration_line
from immich_memories.ui.pages.memory_run import CUT_ALREADY_RUNNING, arm_cut
from immich_memories.ui.pages.step1_config import render_immich_connection
from immich_memories.ui.pages.step1_presets import CUSTOM_RANGE, render_type_params

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

_TITLES: dict[MemoryType, str] = {
    MemoryType.YEAR_IN_REVIEW: "Year in Review",
    MemoryType.SEASON: "Season",
    MemoryType.PERSON_SPOTLIGHT: "Person Spotlight",
    MemoryType.MULTI_PERSON: "Multi-Person",
    MemoryType.MONTHLY_HIGHLIGHTS: "Monthly Highlights",
    MemoryType.ON_THIS_DAY: "On This Day",
    MemoryType.ALBUM: "Album",
    MemoryType.TRIP: "Trip",
    MemoryType.HOLIDAY: "Holiday",
    MemoryType.SPECIAL_DAY: "Surprise me",
}

# The select's rows: the CLI's --memory-type choices in the CLI's order, then
# the custom range the CLI spells as --start/--end.
MEMORY_TYPE_LABELS: dict[str, str] = {
    **{memory_type.value: _TITLES[memory_type] for memory_type in OFFERED_MEMORY_TYPES},
    CUSTOM_RANGE: "Custom date range",
}

# (label, AppState field, tooltip) for the pool switches the selection route reads.
_POOL_SWITCHES = (
    ("Include Photos", "include_photos", "Photographs join the pool as stills the editor can hold"),
    (
        "Include Live Photos",
        "include_live_photos",
        "The editor may play a Live Photo's motion when it earns it",
    ),
    (
        "Accept Forwarded Media",
        "accept_any_provenance",
        "Keep WhatsApp and other received media in the pool for this memory",
    ),
    ("HDR clips only", "hdr_only", "Drop SDR video from the pool"),
)


def begin_cut(state: AppState) -> str | None:
    """Arm a cut from the brief, or say why it cannot start.

    Returns the refusal, or None once armed: the pool is dropped so the cut
    reloads it from the brief, and the Memory page picks the run up on its
    next render.
    """
    if not state.scope_is_selected:
        if state.memory_type == MemoryType.ALBUM:
            return "Pick an album first"
        return "Pick a memory type and a valid period first"
    if not arm_cut(state, before=state.reset_clips):
        return CUT_ALREADY_RUNNING
    return None


def _cut(state: AppState) -> None:
    refusal = begin_cut(state)
    if refusal is not None:
        ui.notify(refusal, type="warning")
        return
    ui.navigate.to("/")


def _render_type_select(state: AppState, params: ui.column) -> None:
    def fill(key: str | None) -> None:
        params.clear()
        if key:
            with params:
                render_type_params(key)

    def on_change(e) -> None:
        state.choose_memory_type(e.value)
        fill(e.value)

    ui.select(
        options=MEMORY_TYPE_LABELS,
        label="Memory type",
        value=state.memory_type,
        on_change=on_change,
    ).classes("w-72")
    fill(state.memory_type)


def _render_pool_switches(state: AppState) -> None:
    with (
        ui.element("div")
        .classes("w-full grid gap-3")
        .style("grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))")
    ):
        for label, field, tip in _POOL_SWITCHES:
            with im_card() as card:
                card.classes("p-3")
                ui.switch(label).bind_value(state, field).props("color=primary").tooltip(tip)


def _render_advanced(state: AppState) -> None:
    with ui.expansion("Advanced", icon="tune").classes("w-full"):
        render_immich_connection(state)
        im_section_header("Pool", icon="photo_library")
        _render_pool_switches(state)
        ui.label(
            "The media pool and the excerpt editor have their own page: exclude what may "
            "never be cut, and trim what was."
        ).classes("text-sm mt-2").style("color: var(--im-text-secondary)")
        im_button(
            "Open the media pool",
            variant="secondary",
            icon="video_library",
            on_click=lambda: ui.navigate.to("/step2"),
        )


def render_brief(state: AppState) -> None:
    """The brief, or the connection panel alone until Immich has answered."""
    if not state.immich_url and state.config:
        state.immich_url = state.config.immich.url
        state.immich_api_key = state.config.immich.api_key
    if not (state.people or state.years):
        render_immich_connection(state)
        im_info_card(
            "Connect to your Immich server to continue. "
            "Enter your server URL and API key above, then click 'Test Connection'.",
            variant="warning",
        )
        return

    im_section_header("What is it about", icon="auto_awesome")
    select_row = ui.row().classes("w-full items-end gap-4")
    params = ui.column().classes("w-full")
    with select_row:
        _render_type_select(state, params)

    im_section_header("How long", icon="timer")
    render_duration_line(state)

    _render_advanced(state)
    im_separator()
    im_button("Cut", variant="primary", icon="content_cut", on_click=lambda: _cut(state)).classes(
        "w-full"
    )
