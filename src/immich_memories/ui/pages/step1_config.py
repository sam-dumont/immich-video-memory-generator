"""Step 1: Configuration page with preset selector and themed components."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

from nicegui import run, ui

from immich_memories.api.immich import ImmichAPIError, SyncImmichClient
from immich_memories.config import Config, get_config, set_config
from immich_memories.security import sanitize_error_message
from immich_memories.timeperiod import (
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
)
from immich_memories.ui.components import (
    im_button,
    im_card,
    im_info_card,
    im_section_header,
    im_separator,
)
from immich_memories.ui.pages.step1_presets import render_preset_selector
from immich_memories.ui.pages.step1_tabs import (
    _render_custom_tab,
    _render_duration_tab,
    _render_year_tab,
)
from immich_memories.ui.state import get_app_state

if TYPE_CHECKING:
    from nicegui.elements.number import Number

logger = logging.getLogger(__name__)


def _render_immich_config_section(state) -> None:
    """Render the Immich connection configuration section."""
    im_section_header("Immich Connection", icon="cloud")

    with im_card() as card:
        card.classes("p-5")

        # Connection status
        connection_status_container = ui.column().classes("w-full")
        if state.connected_user:
            with (
                connection_status_container,
                ui.row()
                .classes("items-center gap-2 p-2 rounded-lg")
                .style("background: rgba(16,185,129,0.1)"),
            ):
                ui.icon("check_circle").style("color: var(--im-success)")
                ui.label(f"Connected as: {state.connected_user}").style("color: var(--im-success)")

        # URL and API key inputs
        with ui.row().classes("w-full gap-4"):
            ui.input(
                "Immich Server URL",
                placeholder="https://photos.example.com",
            ).classes("flex-1").bind_value(state, "immich_url")

            ui.input(
                "API Key",
                password=True,
                password_toggle_button=True,
            ).classes("flex-1").bind_value(state, "immich_api_key")

        # Connection buttons
        status_label = ui.label("").classes("text-sm")

        with ui.row().classes("gap-2 mt-2"):

            async def test_connection() -> None:
                if not state.immich_url or not state.immich_api_key:
                    status_label.set_text("Please enter both URL and API key")
                    status_label.style("color: var(--im-error)")
                    return

                status_label.set_text("Testing connection...")
                status_label.style("color: var(--im-text-secondary)")

                def do_connect():
                    """Run the blocking API calls in a thread pool."""
                    with SyncImmichClient(
                        base_url=state.immich_url,
                        api_key=state.immich_api_key,
                    ) as client:
                        user = client.get_current_user()
                        people = client.get_all_people()
                        years = client.get_available_years()
                        return user, people, years

                try:
                    user, people, years = await run.io_bound(do_connect)
                    state.connected_user = user.name or user.email
                    state.people = people
                    state.years = years

                    status_label.set_text(f"Connected as: {state.connected_user}")
                    status_label.style("color: var(--im-success)")
                    connection_status_container.clear()
                    with (
                        connection_status_container,
                        ui.row()
                        .classes("items-center gap-2 p-2 rounded-lg")
                        .style("background: rgba(16,185,129,0.1)"),
                    ):
                        ui.icon("check_circle").style("color: var(--im-success)")
                        ui.label(f"Connected as: {state.connected_user}").style(
                            "color: var(--im-success)"
                        )
                    ui.navigate.to("/")

                except ImmichAPIError as e:
                    status_label.set_text(f"Connection failed: {sanitize_error_message(str(e))}")
                    status_label.style("color: var(--im-error)")
                except Exception as e:
                    status_label.set_text(f"Error: {sanitize_error_message(str(e))}")
                    status_label.style("color: var(--im-error)")

            def save_config() -> None:
                config = get_config()
                config.immich.url = state.immich_url
                config.immich.api_key = state.immich_api_key
                config_path = Config.get_default_path()
                config.save_yaml(config_path)
                set_config(config)
                state.config_saved = True
                ui.notify("Configuration saved!", type="positive")

            im_button("Test Connection", variant="secondary", on_click=test_connection, icon="wifi")
            im_button("Save Config", variant="secondary", on_click=save_config, icon="save")

        # Auto-connect on page load if credentials are prefilled but not yet connected
        if state.immich_url and state.immich_api_key and not state.connected_user:
            ui.timer(0.1, test_connection, once=True)


def _render_preset_section(state) -> None:
    """Render the memory type preset selector section."""
    im_section_header("Memory Type", icon="auto_awesome")

    def _on_custom_selected(_container) -> None:
        """Render custom date range UI when Custom preset is selected."""
        im_section_header("Custom Date Range", icon="date_range")
        _render_time_period_tabs(state)

    render_preset_selector(on_custom_selected=_on_custom_selected)


def _compute_date_range(state):
    """Compute DateRange from current state. Returns None if not computable."""
    if state.time_period_mode == "year" and state.selected_year:
        if state.year_type == "birthday" and state.birthday:
            return birthday_year(state.birthday, state.selected_year)
        return calendar_year(state.selected_year)
    if state.time_period_mode == "period":
        start = state.custom_start or date.today().replace(month=1, day=1)
        unit_char = "m" if state.period_unit == "months" else "y"
        return from_period(start, f"{state.period_value}{unit_char}")
    if state.time_period_mode == "custom":
        start = state.custom_start or date.today().replace(month=1, day=1)
        end = state.custom_end or date.today()
        return custom_range(start, end)
    return None


def _make_date_range_updater(
    state, date_range_label, duration_input_ref: list
) -> Callable[[], None]:
    """Return a closure that updates the date range display label."""

    def update() -> None:
        try:
            dr = _compute_date_range(state)
            if dr is None:
                date_range_label.set_text("")
                return
            state.date_range = dr
            date_range_label.set_text(f"{dr.description} ({dr.days} days)")
            auto_duration = max(1, min(60, round(dr.days / 365 * 10)))
            state.target_duration = auto_duration
            if duration_input_ref[0] is not None and duration_input_ref[0].value != auto_duration:
                duration_input_ref[0].value = auto_duration
        except Exception as e:
            date_range_label.set_text(f"Invalid date range: {e}")
            date_range_label.style("color: var(--im-error); background: rgba(239,68,68,0.1)")

    return update  # type: ignore[return-value]


def _render_person_filter(state, update_fn, duration_input_ref: list) -> None:
    """Render person filter + target duration row."""
    im_section_header("Person Filter", icon="person")
    named_people = [p for p in state.people if p.name]
    person_options = {"all": "All people"}
    for p in named_people:
        person_options[p.id] = p.name

    with ui.row().classes("w-full gap-4 items-end"):
        person_select = ui.select(options=person_options, label="Person", value="all").classes(
            "w-64"
        )

        def on_person_change(e):
            value = e.value if hasattr(e, "value") else e
            if value == "all":
                state.selected_person = None
                return
            selected = next((p for p in named_people if p.id == value), None)
            state.selected_person = selected
            if selected and selected.birth_date and state.time_period_mode == "year":
                state.birthday = selected.birth_date.date()
                state.year_type = "birthday"
                ui.notify(
                    f"Using {selected.name}'s birthday: {state.birthday.strftime('%B %d, %Y')}",
                    type="info",
                )
                update_fn()

        person_select.on_value_change(on_person_change)

        duration_select = (
            ui.number("Target Duration (minutes)", value=state.target_duration, min=1, max=60)
            .classes("w-48")
            .bind_value(state, "target_duration")
        )
        duration_input_ref[0] = duration_select


def _render_time_period_tabs(state) -> None:
    """Render time period mode tabs (Year / Duration / Custom Range)."""
    _tab_mode_map = {"Year": "year", "Duration": "period", "Custom Range": "custom"}

    with ui.tabs().classes("w-full") as tabs:
        year_tab = ui.tab("Year")
        duration_tab = ui.tab("Duration")
        custom_tab = ui.tab("Custom Range")

    _updater: list = [lambda: None]

    def on_tab_change(e):
        value = e.value if hasattr(e, "value") else e
        if value in _tab_mode_map:
            state.time_period_mode = _tab_mode_map[value]
        _updater[0]()

    tabs.on_value_change(on_tab_change)

    _initial_tab = {"year": year_tab, "period": duration_tab, "custom": custom_tab}.get(
        state.time_period_mode, year_tab
    )

    with ui.tab_panels(tabs, value=_initial_tab).classes("w-full"):
        _render_year_tab(state, year_tab, lambda: _updater[0]())
        _render_duration_tab(state, duration_tab, lambda: _updater[0]())
        _render_custom_tab(state, custom_tab, lambda: _updater[0]())

    # Date range display label
    date_range_label = (
        ui.label("")
        .classes("p-2 rounded-lg mt-4")
        .style("color: var(--im-info); background: rgba(59,130,246,0.1)")
    )
    _duration_input: list[Number | None] = [None]

    update_fn = _make_date_range_updater(state, date_range_label, _duration_input)
    _updater[0] = update_fn
    update_fn()

    _render_person_filter(state, update_fn, _duration_input)


def _render_options_section(state) -> None:
    """Render generation options (live photos, favorites, etc)."""
    im_section_header("Options", icon="settings")

    with im_card() as card:
        card.classes("p-4")
        with ui.column().classes("gap-3 w-full"):
            # Live Photos toggle
            with ui.row().classes("items-center gap-3 w-full"):
                ui.switch("Include Live Photos").bind_value(state, "include_live_photos").props(
                    "color=primary"
                )
                ui.label("3s iPhone clips, burst-merged when consecutive").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )

            # Prioritize favorites
            with ui.row().classes("items-center gap-3 w-full"):
                ui.switch("Prioritize Favorites").bind_value(state, "prioritize_favorites").props(
                    "color=primary"
                )
                ui.label("Rank favorited clips higher in selection").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )


def _render_navigation(state) -> None:
    """Render the Next button at the bottom."""
    im_separator()

    def go_to_step2():
        if not state.date_range:
            ui.notify("Please select a valid time period", type="warning")
            return
        state.step = 2
        state.reset_clips()
        ui.navigate.to("/step2")

    im_button(
        "Next: Review Clips",
        variant="primary",
        on_click=go_to_step2,
        icon="arrow_forward",
    ).classes("w-full")


def render_step1() -> None:
    """Render Step 1: Configuration."""
    state = get_app_state()

    if not state.immich_url:
        config = get_config(reload=True)
        state.immich_url = config.immich.url
        state.immich_api_key = config.immich.api_key

    _render_immich_config_section(state)

    if state.people or state.years:
        im_separator()
        _render_preset_section(state)
        im_separator()
        _render_options_section(state)
        _render_navigation(state)
    else:
        im_info_card(
            "Connect to your Immich server to continue. "
            "Enter your server URL and API key above, then click 'Test Connection'.",
            variant="warning",
        )
