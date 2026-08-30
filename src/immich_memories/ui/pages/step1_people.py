"""Who a memory is about — the wizard's person picking, in one place.

Every card that can be narrowed to people gets the same widget. Multi-person
memories also say whether several names mean everybody together or any named
person. That choice reaches source discovery rather than an editorial prompt.

The renderers take the state and the "apply" callback rather than reaching for
either: the card that owns a memory type is what knows when its parameters are
complete, and injecting the state is what lets these be exercised without a
NiceGUI session behind them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from immich_memories.memory_types.registry import MemoryType

if TYPE_CHECKING:
    from immich_memories.api.models import Person
    from immich_memories.ui.state import AppState

ApplyPreset = Callable[[MemoryType], None]

# The cards that render the shared picker. The two person memory types are
# absent because they collect their people as part of *being* those memories
# and have their own widgets below. So are the three that take no person
# filter at all: an Album is its own pool, a Trip's window is taken whole on
# both surfaces (--person scopes trip detection there, not the fetch), and a
# Special Day is the occasion rather than its guest list.
PERSON_FILTERABLE = frozenset(
    {
        MemoryType.YEAR_IN_REVIEW,
        MemoryType.SEASON,
        MemoryType.MONTHLY_HIGHLIGHTS,
        MemoryType.ON_THIS_DAY,
        MemoryType.HOLIDAY,
        MemoryType.THEN_AND_NOW,
    }
)


def _named_people(state: AppState) -> dict[str, Person]:
    """Immich's roster by name. Unnamed faces cannot be asked for by name."""
    return {person.name: person for person in state.people if person.name}


def render_person_picker(state: AppState, memory_type: MemoryType, apply: ApplyPreset) -> None:
    """An optional person filter, on any memory type that can carry one."""
    by_name = _named_people(state)
    if not by_name:
        return

    saved = state.memory_preset_params.get("person_names") or []

    def on_people(e) -> None:
        chosen = [name for name in (e.value or []) if name in by_name]
        state.memory_preset_params["person_names"] = chosen
        apply(memory_type)

    ui.select(
        options=list(by_name),
        label="Only with (optional)",
        value=[name for name in saved if name in by_name],
        on_change=on_people,
        multiple=True,
    ).props("use-chips").classes("w-64 mt-2").tooltip(
        "Narrow this memory to these people. Pick several and only moments "
        "with all of them are used — the same as --person twice on the CLI."
    )


def render_person_spotlight_params(state: AppState, apply: ApplyPreset) -> None:
    """Year (with All Time) + single person picker + birthday toggle."""
    by_name = _named_people(state)

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years or list(range(2024, 2019, -1))
        year_list = ["All Time"] + [str(y) for y in year_options]
        default_year = year_options[0] if year_options else 2024
        saved_year = state.memory_preset_params.get("year", default_year)
        current_label = "All Time" if saved_year == 0 else str(saved_year)

        def on_year(e) -> None:
            state.memory_preset_params["year"] = 0 if e.value == "All Time" else int(e.value)
            apply(MemoryType.PERSON_SPOTLIGHT)

        ui.select(options=year_list, label="Year", value=current_label, on_change=on_year).classes(
            "w-36"
        )

        saved_person_id = state.memory_preset_params.get("person_id")
        current_name = next((name for name, p in by_name.items() if p.id == saved_person_id), None)

        def on_person(e) -> None:
            selected = by_name.get(e.value)
            if selected:
                state.memory_preset_params["person_id"] = selected.id
                state.memory_preset_params["person_names"] = [e.value]
                # Immich is the source of truth for the anchor, so the mode
                # follows the birth date -- and drops with it, rather than
                # leaving the previous person's date behind to silently
                # decide the window.
                state.memory_preset_params["birthday"] = selected.birth_date
                state.memory_preset_params["use_birthday"] = bool(selected.birth_date)
            apply(MemoryType.PERSON_SPOTLIGHT)

        ui.select(
            options=list(by_name),
            label="Person",
            value=current_name,
            on_change=on_person,
        ).classes("w-48")

    def on_birthday_toggle(e) -> None:
        state.memory_preset_params["use_birthday"] = e.value
        apply(MemoryType.PERSON_SPOTLIGHT)

    anchored = state.memory_preset_params.get("birthday") is not None
    ui.checkbox(
        "Birthday to birthday",
        value=bool(state.memory_preset_params.get("use_birthday")) and anchored,
        on_change=on_birthday_toggle,
    ).classes("mt-2").props("" if anchored else "disable").tooltip(
        "The year runs up to the birthday, and earlier birthdays come with it"
        if anchored
        else "This person has no birth date in Immich — add one under People to unlock this"
    )

    state.memory_preset_params.setdefault("year", saved_year)
    apply(MemoryType.PERSON_SPOTLIGHT)


def render_multi_person_params(state: AppState, apply: ApplyPreset) -> None:
    """Year (with All Time) + multi-person chips (2+ people)."""
    by_name = _named_people(state)

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years or list(range(2024, 2019, -1))
        year_list = ["All Time"] + [str(y) for y in year_options]
        saved_year = state.memory_preset_params.get("year", 0)
        current_label = "All Time" if saved_year == 0 else str(saved_year)

        def on_year(e) -> None:
            state.memory_preset_params["year"] = 0 if e.value == "All Time" else int(e.value)
            apply(MemoryType.MULTI_PERSON)

        ui.select(options=year_list, label="Year", value=current_label, on_change=on_year).classes(
            "w-36"
        )

        saved_names = state.memory_preset_params.get("person_names") or []

        def on_people(e) -> None:
            state.memory_preset_params["person_names"] = [
                name for name in (e.value or []) if name in by_name
            ]
            apply(MemoryType.MULTI_PERSON)

        ui.select(
            options=list(by_name),
            label="People (select 2+)",
            value=[name for name in saved_names if name in by_name],
            on_change=on_people,
            multiple=True,
        ).props("use-chips").classes("w-64")

        saved_match = state.memory_preset_params.get("person_match", "and")

        def on_match(e) -> None:
            state.memory_preset_params["person_match"] = e.value
            apply(MemoryType.MULTI_PERSON)

        ui.toggle(
            {"and": "Together (AND)", "or": "Any of (OR)"},
            value=saved_match,
            on_change=on_match,
        ).classes("mt-1")

    state.memory_preset_params.setdefault("year", saved_year)
    state.memory_preset_params.setdefault("person_match", saved_match)
    apply(MemoryType.MULTI_PERSON)
