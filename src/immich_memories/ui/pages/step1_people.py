"""Who a memory is about — the wizard's person picking, in one place.

Every card that can be narrowed to people gets the same widget, and every card
that can hold two names says whether they mean everybody together or any named
person. That choice reaches source discovery rather than an editorial prompt.
The condition that says what the choice cannot waits under a disclosure.

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
from immich_memories.ui.i18n import N_, tr, tr_options

if TYPE_CHECKING:
    from immich_memories.api.models import Person
    from immich_memories.ui.state import AppState

ApplyPreset = Callable[[MemoryType], None]

# What several names mean, in the words the brief uses on every card that can
# carry more than one. The keys are the `person_match` the fetch reads.
PERSON_MATCH_LABELS = {"and": N_("Together"), "or": N_("Any of these people")}

# The disclosure the Boolean condition lives behind. Picking people and saying
# what several of them mean is the whole plain path; quoted names, AND, OR and
# parentheses are the override, and they wait to be asked for (#887).
PEOPLE_CONDITION_PANEL = N_("Advanced people condition")

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
    }
)


def _named_people(state: AppState) -> dict[str, Person]:
    """Immich's roster by name. Unnamed faces cannot be asked for by name."""
    return {person.name: person for person in state.people if person.name}


def _set_grouped_condition(state: AppState, value: str) -> None:
    """Commit only a complete condition; an invalid edit blocks source loading."""
    from immich_memories.api.person_expression import PersonExpression

    if not value.strip():
        state.clear_person_expression()
        state.memory_preset_params["person_names"] = []
        state.narrow_to_people([])
        return
    try:
        state.set_person_expression(PersonExpression.parse(value))
    except ValueError as exc:
        state.person_expression_error = str(exc)
        raise


def _render_grouped_condition(
    state: AppState, memory_type: MemoryType, apply: ApplyPreset
) -> Callable[[], None]:
    """Offer nested conditions without turning them into a flat people picker."""
    from immich_memories.api.person_expression import PersonExpression

    stored = state.memory_preset_params.get("person_expression")
    saved = PersonExpression.from_dict(stored).display_label if stored is not None else ""
    syncing = False

    def on_condition(e) -> None:
        if syncing:
            return
        try:
            _set_grouped_condition(state, e.value or "")
        except ValueError as exc:
            message.set_text(str(exc))
            return
        expression = state.person_expression
        message.set_text(
            tr("Active condition: {display_label}", display_label=expression.display_label)
            if expression is not None
            else ""
        )
        apply(memory_type)

    # The disclosure opens on a condition that is already in play: a filter the
    # page will not show is a filter nobody can undo.
    with ui.expansion(
        tr(PEOPLE_CONDITION_PANEL),
        icon="tune",
        value=bool(saved or state.person_expression_error),
    ).classes("w-full mt-2"):
        field = ui.input(
            label=tr("Grouped people condition (optional)"),
            value=saved,
            placeholder=tr('("Person A" OR "Person B") AND "Person C"'),
            on_change=on_condition,
        ).classes("w-full")
        ui.label(
            tr(
                "Use quoted names, AND, OR and parentheses. AND requires the people in the same photo or video, not separate pictures from the same event. Changing the people picker replaces this condition."
            )
        ).classes("text-xs")
        message = ui.label(
            state.person_expression_error
            or (tr("Active condition: {display_label}", display_label=saved) if saved else "")
        )

    def clear_field() -> None:
        nonlocal syncing
        syncing = True
        try:
            field.set_value("")
            message.set_text("")
        finally:
            syncing = False

    return clear_field


def render_person_picker(state: AppState, memory_type: MemoryType, apply: ApplyPreset) -> None:
    """An optional person filter, on any memory type that can carry one."""
    by_name = _named_people(state)
    if not by_name:
        return

    saved = [
        name for name in (state.memory_preset_params.get("person_names") or []) if name in by_name
    ]
    saved_match = state.memory_preset_params.get("person_match", "and")
    clear_grouped_display: Callable[[], None] | None = None

    def on_people(e) -> None:
        state.clear_person_expression()
        if clear_grouped_display is not None:
            clear_grouped_display()
        chosen = [name for name in (e.value or []) if name in by_name]
        state.memory_preset_params["person_names"] = chosen
        match_toggle.set_visibility(len(chosen) > 1)
        apply(memory_type)

    def on_match(e) -> None:
        state.clear_person_expression()
        if clear_grouped_display is not None:
            clear_grouped_display()
        state.memory_preset_params["person_match"] = e.value
        apply(memory_type)

    with ui.row().classes("gap-4 items-end flex-wrap"):
        ui.select(
            options=list(by_name),
            label=tr("Only with (optional)"),
            value=saved,
            on_change=on_people,
            multiple=True,
        ).props("use-chips").classes("w-64 mt-2").tooltip(
            tr("Narrow this memory to these people, the same as --person on the CLI.")
        )
        match_toggle = (
            ui.toggle(tr_options(PERSON_MATCH_LABELS), value=saved_match, on_change=on_match)
            .classes("mt-2")
            .tooltip(
                tr(
                    "Together keeps only the moments holding everyone named. Any of these people keeps a moment holding any one of them."
                )
            )
        )
    # One name is not a choice between the two, so the toggle waits for a second.
    match_toggle.set_visibility(len(saved) > 1)
    state.memory_preset_params.setdefault("person_match", saved_match)
    clear_grouped_display = _render_grouped_condition(state, memory_type, apply)


def _anchor_on(state: AppState, selected: Person | None) -> bool:
    """Take the birthday anchor from whoever is selected now; say if there is one.

    Immich is the source of truth, so this is re-read from the selected person
    rather than written once by a change handler: a person restored from saved
    state has to reach the same answer a freshly picked one does. The mode
    follows the birth date and drops with it, so a previous person's date can
    never silently decide the window.

    This is the preset card's anchor -- kwargs for ``create_preset`` -- and is
    deliberately not ``state.birthday``, which belongs to the custom-range tabs
    and carries their own picker's override.
    """
    birth_date = selected.birth_date if selected is not None else None
    if birth_date is None:
        state.memory_preset_params.pop("birthday", None)
        state.memory_preset_params["use_birthday"] = False
        return False
    state.memory_preset_params["birthday"] = birth_date
    # Immich having a date is what offers the mode; unticking it is the user's.
    state.memory_preset_params.setdefault("use_birthday", True)
    return True


def _birthday_tooltip(anchored: bool, *, has_person: bool) -> str:
    """Why the anchor is or is not on offer, naming the field that decides it.

    The disabled state used to read as a verdict on the selected person even
    when none was selected, and pointed at a People page this product does not
    have: the birth date lives on Immich's person, and that is where a user has
    to go to unlock this.
    """
    if anchored:
        return "The year runs up to the birthday, and earlier birthdays come with it"
    if not has_person:
        return "Pick a person first: their birth date in Immich is what anchors this"
    return (
        "This person has no birth date in Immich. Add it there — People → the person "
        "→ edit → birth date — and every birthday memory follows it."
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

        ui.select(
            options={year: tr("All Time") if year == "All Time" else year for year in year_list},
            label=tr("Year"),
            value=current_label,
            on_change=on_year,
        ).classes("w-36")

        saved_person_id = state.memory_preset_params.get("person_id")
        current_name = next((name for name, p in by_name.items() if p.id == saved_person_id), None)

        def on_person(e) -> None:
            state.clear_person_expression()
            selected = by_name.get(e.value)
            if selected:
                state.memory_preset_params["person_id"] = selected.id
                state.memory_preset_params["person_names"] = [e.value]
            # A new pick is answered by Immich afresh: the previous person's
            # choice of mode is not evidence about this one.
            state.memory_preset_params.pop("use_birthday", None)
            _anchor_on(state, selected)
            apply(MemoryType.PERSON_SPOTLIGHT)

        ui.select(
            options=list(by_name),
            label=tr("Person"),
            value=current_name,
            on_change=on_person,
        ).classes("w-48")

    def on_birthday_toggle(e) -> None:
        state.memory_preset_params["use_birthday"] = e.value
        apply(MemoryType.PERSON_SPOTLIGHT)

    selected_person = by_name[current_name] if current_name else None
    anchored = _anchor_on(state, selected_person)
    ui.checkbox(
        tr("Birthday to birthday"),
        value=bool(state.memory_preset_params.get("use_birthday")),
        on_change=on_birthday_toggle,
    ).classes("mt-2").props("" if anchored else "disable").tooltip(
        _birthday_tooltip(anchored, has_person=selected_person is not None)
    )

    state.memory_preset_params.setdefault("year", saved_year)
    apply(MemoryType.PERSON_SPOTLIGHT)


def render_multi_person_params(state: AppState, apply: ApplyPreset) -> None:
    """Year (with All Time) + multi-person chips (2+ people)."""
    by_name = _named_people(state)
    clear_grouped_display: Callable[[], None] | None = None

    with ui.row().classes("gap-4 items-end flex-wrap"):
        year_options = state.years or list(range(2024, 2019, -1))
        year_list = ["All Time"] + [str(y) for y in year_options]
        saved_year = state.memory_preset_params.get("year", 0)
        current_label = "All Time" if saved_year == 0 else str(saved_year)

        def on_year(e) -> None:
            state.memory_preset_params["year"] = 0 if e.value == "All Time" else int(e.value)
            apply(MemoryType.MULTI_PERSON)

        ui.select(
            options={year: tr("All Time") if year == "All Time" else year for year in year_list},
            label=tr("Year"),
            value=current_label,
            on_change=on_year,
        ).classes("w-36")

        saved_names = state.memory_preset_params.get("person_names") or []

        def on_people(e) -> None:
            state.clear_person_expression()
            if clear_grouped_display is not None:
                clear_grouped_display()
            state.memory_preset_params["person_names"] = [
                name for name in (e.value or []) if name in by_name
            ]
            apply(MemoryType.MULTI_PERSON)

        ui.select(
            options=list(by_name),
            label=tr("People (select 2+)"),
            value=[name for name in saved_names if name in by_name],
            on_change=on_people,
            multiple=True,
        ).props("use-chips").classes("w-64")

        saved_match = state.memory_preset_params.get("person_match", "and")

        def on_match(e) -> None:
            state.clear_person_expression()
            if clear_grouped_display is not None:
                clear_grouped_display()
            state.memory_preset_params["person_match"] = e.value
            apply(MemoryType.MULTI_PERSON)

        ui.toggle(
            tr_options(PERSON_MATCH_LABELS),
            value=saved_match,
            on_change=on_match,
        ).classes("mt-1")

    state.memory_preset_params.setdefault("year", saved_year)
    state.memory_preset_params.setdefault("person_match", saved_match)
    clear_grouped_display = _render_grouped_condition(state, MemoryType.MULTI_PERSON, apply)
    apply(MemoryType.MULTI_PERSON)
