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
from immich_memories.ui.i18n import N_, tr, tr_options
from immich_memories.ui.pages.memory_duration import render_duration_line
from immich_memories.ui.pages.memory_run import CUT_ALREADY_RUNNING, arm_cut
from immich_memories.ui.pages.step1_config import render_immich_connection
from immich_memories.ui.pages.step1_presets import CUSTOM_RANGE, render_type_params

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

_TITLES: dict[MemoryType, str] = {
    MemoryType.YEAR_IN_REVIEW: N_("Year in Review"),
    MemoryType.SEASON: N_("Season"),
    MemoryType.PERSON_SPOTLIGHT: N_("Person Spotlight"),
    MemoryType.MULTI_PERSON: N_("Multi-Person"),
    MemoryType.MONTHLY_HIGHLIGHTS: N_("Monthly Highlights"),
    MemoryType.ON_THIS_DAY: N_("On This Day"),
    MemoryType.ALBUM: N_("Album"),
    MemoryType.TRIP: N_("Trip"),
    MemoryType.HOLIDAY: N_("Holiday"),
    MemoryType.SPECIAL_DAY: N_("Surprise me"),
}

# The select's rows: the CLI's --memory-type choices in the CLI's order, then
# the custom range the CLI spells as --start/--end.
MEMORY_TYPE_LABELS: dict[str, str] = {
    **{memory_type.value: _TITLES[memory_type] for memory_type in OFFERED_MEMORY_TYPES},
    CUSTOM_RANGE: N_("Custom date range"),
}

# (label, AppState field, tooltip) for the pool switches the selection route reads.
_POOL_SWITCHES = (
    (
        N_("Include Photos"),
        "include_photos",
        N_("Photographs join the pool as stills the editor can hold"),
    ),
    (
        N_("Include Live Photos"),
        "include_live_photos",
        N_("The editor may play a Live Photo's motion when it earns it"),
    ),
    (
        N_("Accept Forwarded Media"),
        "accept_any_provenance",
        N_("Keep WhatsApp and other received media in the pool for this memory"),
    ),
    (N_("HDR clips only"), "hdr_only", N_("Drop SDR video from the pool")),
)


SHARING_LABELS: dict[str, str] = {
    "just-us": N_("Just us"),
    "family": N_("Family"),
    "shareable": N_("Shareable"),
}
_SHARING_LINES = {
    "just-us": N_("The household. Private moments a caption names, like a bath, play too."),
    "family": N_("Grandparents, siblings, the group chat. Private moments stay out."),
    "shareable": N_(
        "Anyone. Only pictures nothing held back: no detector flag, no private moment."
    ),
}


def chosen_sharing(state: AppState) -> str:
    """The level the next cut is for: the brief's choice, else the config's default."""
    default = state.config.defaults.sharing if state.config else "family"
    return state.sharing or default


def begin_cut(state: AppState) -> str | None:
    """Arm a cut from the brief, or say why it cannot start.

    Returns the refusal, or None once armed: the pool is dropped so the cut
    reloads it from the brief, and the Memory page picks the run up on its
    next render.
    """
    if not state.scope_is_selected:
        if state.memory_type == MemoryType.ALBUM:
            return tr("Pick an album first")
        return tr("Pick a memory type and a valid period first")
    if state.config is not None:
        from immich_memories.analysis.editorial_shareability_tiers import sharing_refusal

        refusal = sharing_refusal(state.config, chosen_sharing(state))
        if refusal:
            return refusal
    if not arm_cut(state, before=state.reset_clips):
        return tr(CUT_ALREADY_RUNNING)
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
        options=tr_options(MEMORY_TYPE_LABELS),
        label=tr("Memory type"),
        value=state.memory_type,
        on_change=on_change,
    ).classes("w-72")
    fill(state.memory_type)


def _render_sharing(state: AppState) -> None:
    level = chosen_sharing(state)
    select = ui.select(options=tr_options(SHARING_LABELS), label=tr("Sharing"), value=level)
    select.classes("w-72")
    line = ui.label(tr(_SHARING_LINES[level])).classes("text-sm sharing-line")
    line.style("color: var(--im-text-secondary)")

    def on_change(e) -> None:
        state.sharing = e.value
        line.set_text(tr(_SHARING_LINES[e.value]))

    select.on_value_change(on_change)


def _render_pool_switches(state: AppState) -> None:
    with (
        ui.element("div")
        .classes("w-full grid gap-3")
        .style("grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))")
    ):
        for label, field, tip in _POOL_SWITCHES:
            with im_card() as card:
                card.classes("p-3")
                ui.switch(tr(label)).bind_value(state, field).props("color=primary").tooltip(
                    tr(tip)
                )


def _render_advanced(state: AppState) -> None:
    with ui.expansion(tr("Advanced"), icon="tune").classes("w-full"):
        render_immich_connection(state)
        im_section_header(tr("Pool"), icon="photo_library")
        _render_pool_switches(state)
        ui.label(
            tr(
                "The media pool and the excerpt editor have their own page: exclude what may never be cut, and trim what was."
            )
        ).classes("text-sm mt-2").style("color: var(--im-text-secondary)")
        im_button(
            tr("Open the media pool"),
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
            tr(
                "Connect to your Immich server to continue. Enter your server URL and API key above, then click 'Test Connection'."
            ),
            variant="warning",
        )
        return

    if state.cut_failure:
        im_info_card(state.cut_failure, variant="error")
    im_section_header(tr("What is it about"), icon="auto_awesome")
    select_row = ui.row().classes("w-full items-end gap-4")
    params = ui.column().classes("w-full")
    with select_row:
        _render_type_select(state, params)

    im_section_header(tr("How long"), icon="timer")
    render_duration_line(state)

    im_section_header(tr("Who will watch it"), icon="group")
    _render_sharing(state)

    _render_advanced(state)
    im_separator()
    im_button(
        tr("Cut"), variant="primary", icon="content_cut", on_click=lambda: _cut(state)
    ).classes("w-full")
