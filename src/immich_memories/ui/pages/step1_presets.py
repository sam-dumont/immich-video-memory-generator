"""Memory type preset selector for Step 1."""

from __future__ import annotations

import calendar as cal
import logging

from nicegui import ui

from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType
from immich_memories.ui.components import im_card
from immich_memories.ui.state import get_app_state

logger = logging.getLogger(__name__)

# Preset card metadata: (key, icon, title, description)
# key is either a MemoryType value or "custom"
_PRESET_CARDS: list[tuple[str, str, str, str]] = [
    (MemoryType.YEAR_IN_REVIEW, "calendar_today", "Year in Review", "Your year, one video"),
    (MemoryType.SEASON, "wb_sunny", "Season", "Best moments of the season"),
    (MemoryType.PERSON_SPOTLIGHT, "person", "Person Spotlight", "A year through their eyes"),
    (MemoryType.MULTI_PERSON, "group", "Multi-Person", "Together moments"),
    (MemoryType.MONTHLY_HIGHLIGHTS, "event_note", "Monthly Highlights", "One month, distilled"),
    (MemoryType.ON_THIS_DAY, "history", "On This Day", "This day through the years"),
    (MemoryType.TRIP, "flight_takeoff", "Trip", "Auto-detect trips from GPS data"),
    ("custom", "tune", "Custom", "Full control over date range"),
]

_SEASONS = ["spring", "summer", "autumn", "winter"]
_MONTHS = {i: cal.month_name[i] for i in range(1, 13)}


def render_preset_selector(on_custom_selected=None) -> None:
    """Render the memory type preset selection grid and parameter panel.

    Args:
        on_custom_selected: Callback(container) called when "Custom" preset is selected.
            Receives a ui.column container to render the custom date range UI into.
    """
    state = get_app_state()
    params_container = ui.column().classes("w-full")

    def select_preset(key: str) -> None:
        state.memory_type = key
        state.memory_preset_params = {}
        params_container.clear()
        with params_container:
            _render_params(key)
            if key == "custom" and on_custom_selected:
                on_custom_selected(params_container)
        # Re-render cards to update selection highlight
        _rebuild_grid()

    grid_container = ui.element("div")

    def _rebuild_grid() -> None:
        grid_container.clear()
        with (
            grid_container,
            ui.element("div").classes("grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3"),
        ):
            for key, icon, title, desc in _PRESET_CARDS:
                is_selected = state.memory_type == key
                with im_card(interactive=True) as card:
                    card.classes("p-3").style(
                        "display:flex;align-items:center;justify-content:center"
                    )
                    if is_selected:
                        card.classes("im-preset-selected")
                    card.on("click", lambda _e, k=key: select_preset(k))
                    with ui.column().classes("items-center gap-1 py-1 w-full"):
                        ui.icon(icon).classes("text-2xl").style("color: var(--im-primary)")
                        ui.label(title).classes("text-sm font-semibold text-center").style(
                            "color: var(--im-text)"
                        )
                        ui.label(desc).classes("text-xs text-center").style(
                            "color: var(--im-text-secondary)"
                        )

    _rebuild_grid()

    # Render params for the currently selected preset
    if state.memory_type:
        with params_container:
            _render_params(state.memory_type)
            if state.memory_type == "custom" and on_custom_selected:
                on_custom_selected(params_container)


def _render_params(key: str) -> None:
    """Render conditional parameter fields based on selected preset."""
    if key == "custom":
        # Custom mode — no params here, the advanced date range section handles it
        ui.label("Configure your date range below.").style(
            "color: var(--im-text-secondary)"
        ).classes("text-sm italic mt-2")
        return

    try:
        memory_type = MemoryType(key)
    except ValueError:
        return

    with im_card() as card:
        card.classes("p-4 mt-3")

        if memory_type == MemoryType.YEAR_IN_REVIEW:
            _render_year_picker()
        elif memory_type == MemoryType.SEASON:
            _render_season_params()
        elif memory_type == MemoryType.PERSON_SPOTLIGHT:
            _render_person_spotlight_params()
        elif memory_type == MemoryType.MULTI_PERSON:
            _render_multi_person_params()
        elif memory_type == MemoryType.MONTHLY_HIGHLIGHTS:
            _render_monthly_params()
        elif memory_type == MemoryType.ON_THIS_DAY:
            ui.label("Automatically uses today's date across previous years").style(
                "color: var(--im-text-secondary)"
            ).classes("text-sm italic")
            _apply_preset_to_state(memory_type)
        elif memory_type == MemoryType.TRIP:
            _render_trip_params()


def _year_options_with_all() -> list:
    """Return year options including 'All Time'."""
    state = get_app_state()
    years = state.years if state.years else list(range(2024, 2019, -1))
    return [("all", "All Time")] + [(y, str(y)) for y in years]


def _render_year_picker() -> None:
    """Year picker shared by multiple presets."""
    state = get_app_state()
    year_options = state.years if state.years else list(range(2024, 2019, -1))
    current = state.memory_preset_params.get("year", year_options[0] if year_options else 2024)

    def on_change(e):
        state.memory_preset_params["year"] = e.value
        _apply_preset_to_state(MemoryType(state.memory_type))  # type: ignore[arg-type]

    ui.select(options=year_options, label="Year", value=current, on_change=on_change).classes(
        "w-48"
    )
    state.memory_preset_params.setdefault("year", current)
    _apply_preset_to_state(MemoryType(state.memory_type))  # type: ignore[arg-type]


def _render_season_params() -> None:
    """Year + Season + Hemisphere pickers."""
    state = get_app_state()

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years if state.years else list(range(2024, 2019, -1))
        current_year = state.memory_preset_params.get(
            "year", year_options[0] if year_options else 2024
        )

        def on_year(e):
            state.memory_preset_params["year"] = e.value
            _apply_preset_to_state(MemoryType.SEASON)

        ui.select(
            options=year_options, label="Year", value=current_year, on_change=on_year
        ).classes("w-36")

        current_season = state.memory_preset_params.get("season", "summer")

        def on_season(e):
            state.memory_preset_params["season"] = e.value
            _apply_preset_to_state(MemoryType.SEASON)

        ui.select(
            options=_SEASONS, label="Season", value=current_season, on_change=on_season
        ).classes("w-36")

        current_hemi = state.memory_preset_params.get("hemisphere", "north")

        def on_hemi(e):
            state.memory_preset_params["hemisphere"] = e.value
            _apply_preset_to_state(MemoryType.SEASON)

        ui.select(
            options=["north", "south"], label="Hemisphere", value=current_hemi, on_change=on_hemi
        ).classes("w-36")

    state.memory_preset_params.setdefault("year", current_year)
    state.memory_preset_params.setdefault("season", current_season)
    state.memory_preset_params.setdefault("hemisphere", current_hemi)
    _apply_preset_to_state(MemoryType.SEASON)


def _render_person_spotlight_params() -> None:
    """Year (with All Time) + single person picker + birthday toggle."""
    state = get_app_state()
    named_people = [p for p in state.people if p.name]
    name_to_person = {p.name: p for p in named_people}

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years if state.years else list(range(2024, 2019, -1))
        year_list = ["All Time"] + [str(y) for y in year_options]
        default_year = year_options[0] if year_options else 2024
        saved_year = state.memory_preset_params.get("year", default_year)
        current_label = "All Time" if saved_year == 0 else str(saved_year)

        def on_year(e):
            val = 0 if e.value == "All Time" else int(e.value)
            state.memory_preset_params["year"] = val
            _apply_preset_to_state(MemoryType.PERSON_SPOTLIGHT)

        ui.select(options=year_list, label="Year", value=current_label, on_change=on_year).classes(
            "w-36"
        )

        person_names = [p.name for p in named_people]
        saved_person_id = state.memory_preset_params.get("person_id")
        current_name = next((p.name for p in named_people if p.id == saved_person_id), None)

        def on_person(e):
            selected = name_to_person.get(e.value)
            if selected:
                state.memory_preset_params["person_id"] = selected.id
                state.memory_preset_params["person_names"] = [selected.name]
                state.selected_person = selected
                # Auto-enable birthday mode if person has a birth_date
                if hasattr(selected, "birth_date") and selected.birth_date:
                    state.memory_preset_params["use_birthday"] = True
                    state.memory_preset_params["birthday"] = selected.birth_date
            _apply_preset_to_state(MemoryType.PERSON_SPOTLIGHT)

        ui.select(
            options=person_names,
            label="Person",
            value=current_name,
            on_change=on_person,
        ).classes("w-48")

    # Birthday-to-birthday toggle
    def on_birthday_toggle(e):
        state.memory_preset_params["use_birthday"] = e.value
        _apply_preset_to_state(MemoryType.PERSON_SPOTLIGHT)

    ui.checkbox(
        "Birthday to birthday",
        value=state.memory_preset_params.get("use_birthday", False),
        on_change=on_birthday_toggle,
    ).classes("mt-2").tooltip("Year runs from birthday to birthday instead of January to December")

    state.memory_preset_params.setdefault("year", saved_year)
    _apply_preset_to_state(MemoryType.PERSON_SPOTLIGHT)


def _render_multi_person_params() -> None:
    """Year (with All Time) + multi-person chips (2+ people)."""
    state = get_app_state()
    named_people = [p for p in state.people if p.name]
    name_to_person = {p.name: p for p in named_people}

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years if state.years else list(range(2024, 2019, -1))
        year_list = ["All Time"] + [str(y) for y in year_options]
        saved_year = state.memory_preset_params.get("year", 0)
        current_label = "All Time" if saved_year == 0 else str(saved_year)

        def on_year(e):
            val = 0 if e.value == "All Time" else int(e.value)
            state.memory_preset_params["year"] = val
            _apply_preset_to_state(MemoryType.MULTI_PERSON)

        ui.select(options=year_list, label="Year", value=current_label, on_change=on_year).classes(
            "w-36"
        )

        person_names = [p.name for p in named_people]
        saved_ids = state.memory_preset_params.get("person_ids", [])
        current_names = [p.name for p in named_people if p.id in saved_ids]

        def on_people(e):
            selected_names = list(e.value) if e.value else []
            selected = [name_to_person[n] for n in selected_names if n in name_to_person]
            state.memory_preset_params["person_ids"] = [p.id for p in selected]
            state.memory_preset_params["person_names"] = [p.name for p in selected]
            _apply_preset_to_state(MemoryType.MULTI_PERSON)

        ui.select(
            options=person_names,
            label="People (select 2+)",
            value=current_names,
            on_change=on_people,
            multiple=True,
        ).props("use-chips").classes("w-64")

    state.memory_preset_params.setdefault("year", saved_year)
    _apply_preset_to_state(MemoryType.MULTI_PERSON)


def _render_monthly_params() -> None:
    """Year + month picker."""
    state = get_app_state()

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years if state.years else list(range(2024, 2019, -1))
        current_year = state.memory_preset_params.get(
            "year", year_options[0] if year_options else 2024
        )

        def on_year(e):
            state.memory_preset_params["year"] = e.value
            _apply_preset_to_state(MemoryType.MONTHLY_HIGHLIGHTS)

        ui.select(
            options=year_options, label="Year", value=current_year, on_change=on_year
        ).classes("w-36")

        current_month = state.memory_preset_params.get("month", 1)

        def on_month(e):
            state.memory_preset_params["month"] = e.value
            _apply_preset_to_state(MemoryType.MONTHLY_HIGHLIGHTS)

        ui.select(options=_MONTHS, label="Month", value=current_month, on_change=on_month).classes(
            "w-48"
        )

    state.memory_preset_params.setdefault("year", current_year)
    state.memory_preset_params.setdefault("month", current_month)
    _apply_preset_to_state(MemoryType.MONTHLY_HIGHLIGHTS)


def _render_trip_params() -> None:
    """Year picker + dynamic trip detection dropdown."""
    from nicegui import run

    from immich_memories.analysis.trip_detection import DetectedTrip

    state = get_app_state()

    year_options = state.years if state.years else list(range(2024, 2019, -1))
    current_year = state.memory_preset_params.get("year", year_options[0] if year_options else 2024)

    # Trip detection results container
    trip_container = ui.column().classes("w-full mt-2")

    def _build_trip_options(trips: list[DetectedTrip]) -> dict[int, str]:
        """Build {index: label} map for the trip dropdown."""
        options: dict[int, str] = {}
        for i, t in enumerate(trips):
            days = (t.end_date - t.start_date).days + 1
            options[i] = (
                f"{t.location_name} ({t.start_date} to {t.end_date}, {days}d, {t.asset_count} videos)"
            )
        return options

    async def _detect_trips_for_year(year_val: int) -> None:
        """Fetch videos and run trip detection for the selected year."""
        trip_container.clear()

        if not state.immich_url or not state.immich_api_key:
            with trip_container:
                ui.label("Connect to Immich first to detect trips.").style(
                    "color: var(--im-text-secondary)"
                ).classes("text-sm italic")
            return

        with trip_container:
            spinner_row = ui.row().classes("items-center gap-2")
            with spinner_row:
                ui.spinner(size="sm")
                ui.label("Detecting trips from GPS data...").style(
                    "color: var(--im-text-secondary)"
                ).classes("text-sm")

        try:
            from immich_memories.analysis.trip_detection import detect_trips
            from immich_memories.api.immich import SyncImmichClient
            from immich_memories.config import get_config
            from immich_memories.timeperiod import DateRange

            config = get_config()
            trips_config = config.trips

            def do_detect() -> list[DetectedTrip]:
                from datetime import datetime

                dr = DateRange(
                    start=datetime(year_val, 1, 1, 0, 0, 0),
                    end=datetime(year_val, 12, 31, 23, 59, 59),
                )
                with SyncImmichClient(
                    base_url=state.immich_url,
                    api_key=state.immich_api_key,
                ) as client:
                    assets = client.get_videos_for_date_range(dr)
                return detect_trips(
                    assets,
                    trips_config.homebase_latitude,
                    trips_config.homebase_longitude,
                    min_distance_km=trips_config.min_distance_km,
                    min_duration_days=trips_config.min_duration_days,
                    max_gap_days=trips_config.max_gap_days,
                )

            detected = await run.io_bound(do_detect)
            state.detected_trips = detected
        except Exception as exc:
            logger.warning("Trip detection failed: %s", exc)
            trip_container.clear()
            with trip_container:
                ui.label(f"Trip detection failed: {exc}").style("color: var(--im-error)").classes(
                    "text-sm"
                )
            return

        trip_container.clear()
        with trip_container:
            if not detected:
                ui.label("No trips detected for this year.").style(
                    "color: var(--im-text-secondary)"
                ).classes("text-sm italic")
                return

            ui.label(f"Found {len(detected)} trip(s):").style(
                "color: var(--im-text-secondary)"
            ).classes("text-sm mb-1")

            trip_options = _build_trip_options(detected)
            saved_idx = state.memory_preset_params.get("trip_index")

            def on_trip(e):
                idx = e.value
                trip = detected[idx]
                p = state.memory_preset_params
                p["trip_index"] = idx
                p["trip_start"] = trip.start_date
                p["trip_end"] = trip.end_date
                p["location_name"] = trip.location_name
                p["asset_count"] = trip.asset_count
                p["home_lat"] = trips_config.homebase_latitude
                p["home_lon"] = trips_config.homebase_longitude
                p["min_distance_km"] = trips_config.min_distance_km
                _apply_preset_to_state(MemoryType.TRIP)

            ui.select(
                options=trip_options,
                label="Select a trip",
                value=saved_idx,
                on_change=on_trip,
            ).classes("w-full")

    async def on_year_change(e):
        state.memory_preset_params["year"] = e.value
        for k in ("trip_index", "trip_start", "trip_end", "location_name"):
            state.memory_preset_params.pop(k, None)
        state.detected_trips = []
        state.date_range = None
        await _detect_trips_for_year(e.value)

    ui.select(
        options=year_options, label="Year", value=current_year, on_change=on_year_change
    ).classes("w-48")

    state.memory_preset_params.setdefault("year", current_year)

    # Auto-detect trips if connected and year is set
    if state.connected_user and current_year:
        ui.timer(0.1, lambda: _detect_trips_for_year(current_year), once=True)


def _apply_preset_to_state(memory_type: MemoryType) -> None:
    """Create a preset and write its date_range + duration into app state."""
    from immich_memories.timeperiod import custom_range

    state = get_app_state()
    params = state.memory_preset_params

    # "All Time" → wide date range covering all years
    if params.get("year") == 0:
        from datetime import date

        state.date_range = custom_range(date(2000, 1, 1), date.today())
        state.target_duration = 10
        return

    try:
        preset = create_preset(memory_type, **params)
        if preset.date_ranges:
            state.date_range = preset.date_ranges[0]
        if preset.default_duration_minutes:
            state.target_duration = preset.default_duration_minutes
        elif preset.date_ranges:
            # Auto-compute duration from date range: ~1 min/month, ~8 min/year
            days = preset.date_ranges[0].days
            state.target_duration = max(1, min(10, round(days / 45)))
    except (ValueError, TypeError) as exc:
        # Preset not fully configured yet (e.g. no person selected) — clear stale date range
        state.date_range = None
        logger.debug("Preset not ready yet: %s", exc)
