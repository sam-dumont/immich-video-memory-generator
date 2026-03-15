"""Tab panel renderers for Step 1: Year, Duration, Custom Range tabs."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from nicegui import ui

from immich_memories.timeperiod import available_years

if TYPE_CHECKING:
    from nicegui.elements.menu import Menu


def _render_birthday_picker(state, update_fn, birthday_container: ui.element) -> None:
    """Render or clear the birthday date picker inside birthday_container."""
    birthday_container.clear()
    if state.year_type != "birthday":
        return
    with birthday_container:
        current_bday = state.birthday or date(2000, 1, 1)
        bday_menu: Menu
        with ui.input("Birthday") as bday_input:
            with bday_input.add_slot("prepend"):
                ui.icon("event").on("click", lambda: bday_menu.open()).classes("cursor-pointer")  # noqa: FURB111
            with ui.menu().props("no-auto-close") as bday_menu:

                def on_date_pick(e):
                    picked = e.value if hasattr(e, "value") else e
                    if picked:
                        state.birthday = date.fromisoformat(picked)
                        bday_input.value = picked
                        bday_menu.close()
                        update_fn()

                ui.date(value=current_bday.isoformat(), on_change=on_date_pick)
        bday_input.value = current_bday.strftime("%Y-%m-%d")


def _render_year_type_buttons(
    state, calendar_btn, birthday_btn, birthday_container, update_fn
) -> None:
    """Wire year-type toggle buttons after they're created."""

    def set_year_type(year_type: str) -> None:
        state.year_type = year_type
        if year_type == "calendar":
            calendar_btn.props(remove="flat")
            birthday_btn.props("flat")
        else:
            calendar_btn.props("flat")
            birthday_btn.props(remove="flat")
        _render_birthday_picker(state, update_fn, birthday_container)
        update_fn()

    calendar_btn.on("click", lambda: set_year_type("calendar"))
    birthday_btn.on("click", lambda: set_year_type("birthday"))


def _render_year_selector_row(state, update_fn) -> tuple:
    """Render year select + type buttons row. Returns (calendar_btn, birthday_btn)."""
    year_options = state.years or available_years()
    year_select = ui.select(
        options=year_options,
        label="Year",
        value=state.selected_year or (year_options[0] if year_options else None),
    ).classes("w-48")

    def on_year_change(e):
        state.selected_year = e.value if hasattr(e, "value") else e
        update_fn()

    year_select.on_value_change(on_year_change)
    if not state.selected_year and year_options:
        state.selected_year = year_options[0]

    with ui.column().classes("gap-1"):
        ui.label("Year type").classes("text-sm").style("color: var(--im-text-secondary)")
        with ui.row().classes("gap-2"):
            calendar_btn = ui.button("Calendar Year").props(
                "flat" if state.year_type != "calendar" else ""
            )
            birthday_btn = ui.button("From Birthday").props(
                "flat" if state.year_type != "birthday" else ""
            )
    return calendar_btn, birthday_btn


def _render_year_tab(state, tab, update_fn) -> None:
    """Render the Year tab panel."""
    with ui.tab_panel(tab):
        with ui.row().classes("w-full gap-4 items-end"):
            calendar_btn, birthday_btn = _render_year_selector_row(state, update_fn)

        birthday_container = ui.column().classes("mt-4")
        _render_year_type_buttons(state, calendar_btn, birthday_btn, birthday_container, update_fn)
        _render_birthday_picker(state, update_fn, birthday_container)


def _render_duration_tab(state, tab, update_fn) -> None:
    """Render the Duration tab panel."""
    with ui.tab_panel(tab), ui.row().classes("w-full gap-4 items-end"):
        duration_input = ui.number("Duration", value=state.period_value, min=1, max=24).classes(
            "w-24"
        )

        def on_duration_change(e):
            state.period_value = int(e.value if hasattr(e, "value") else e)
            update_fn()

        duration_input.on_value_change(on_duration_change)

        unit_select = ui.select(
            options=["Months", "Years"],
            label="Unit",
            value="Months" if state.period_unit == "months" else "Years",
        ).classes("w-32")

        def on_unit_change(e):
            state.period_unit = (e.value if hasattr(e, "value") else e).lower()
            update_fn()

        unit_select.on_value_change(on_unit_change)

        period_start_menu: Menu
        with ui.input("Starting from") as start_input:
            with start_input.add_slot("prepend"):
                ui.icon("event").on("click", lambda: period_start_menu.open()).classes(  # noqa: FURB111
                    "cursor-pointer"
                )
            with ui.menu().props("no-auto-close") as period_start_menu:
                start_val = state.custom_start or date.today().replace(month=1, day=1)

                def on_period_start_pick(e):
                    val = e.value if hasattr(e, "value") else e
                    if val:
                        state.custom_start = date.fromisoformat(val)
                        start_input.value = val
                        period_start_menu.close()
                        update_fn()

                ui.date(value=start_val.isoformat(), on_change=on_period_start_pick)
        start_input.value = (state.custom_start or date.today().replace(month=1, day=1)).strftime(
            "%Y-%m-%d"
        )


def _render_custom_tab(state, tab, update_fn) -> None:
    """Render the Custom Range tab panel."""
    with ui.tab_panel(tab), ui.row().classes("w-full gap-4 items-end"):
        start_val = state.custom_start or date.today().replace(month=1, day=1)
        _render_custom_start_picker(state, update_fn, start_val)
        end_val = state.custom_end or date.today()
        _render_custom_end_picker(state, update_fn, end_val)


def _render_custom_start_picker(state, update_fn, start_val: date) -> None:
    """Render the custom range start date picker."""
    custom_start_menu: Menu
    with ui.input("Start date") as custom_start_input:
        with custom_start_input.add_slot("prepend"):
            ui.icon("event").on("click", lambda: custom_start_menu.open()).classes("cursor-pointer")  # noqa: FURB111
        with ui.menu().props("no-auto-close") as custom_start_menu:

            def on_pick(e):
                val = e.value if hasattr(e, "value") else e
                if val:
                    state.custom_start = date.fromisoformat(val)
                    custom_start_input.value = val
                    custom_start_menu.close()
                    update_fn()

            ui.date(value=start_val.isoformat(), on_change=on_pick)
    custom_start_input.value = start_val.strftime("%Y-%m-%d")


def _render_custom_end_picker(state, update_fn, end_val: date) -> None:
    """Render the custom range end date picker."""
    custom_end_menu: Menu
    with ui.input("End date") as custom_end_input:
        with custom_end_input.add_slot("prepend"):
            ui.icon("event").on("click", lambda: custom_end_menu.open()).classes("cursor-pointer")  # noqa: FURB111
        with ui.menu().props("no-auto-close") as custom_end_menu:

            def on_pick(e):
                val = e.value if hasattr(e, "value") else e
                if val:
                    state.custom_end = date.fromisoformat(val)
                    custom_end_input.value = val
                    custom_end_menu.close()
                    update_fn()

            ui.date(value=end_val.isoformat(), on_change=on_pick)
    custom_end_input.value = end_val.strftime("%Y-%m-%d")
