"""The earliest day a people condition can be satisfied, read off the people's birth dates."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from immich_memories.api.models import Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.timeperiod import DateRange, custom_range

# What bounded the start, in terms of the rule applied at the top of the condition.
# A name never appears here: this sentence is logged and filed with the run.
_ORIGIN = {
    "person": "the window starts at the named person's birth date",
    "all": "the window starts at the youngest birth date the people it needs allow",
    "any": "the window starts at the oldest birth date among the people it accepts",
}


@dataclass(frozen=True, slots=True)
class DerivedPeopleWindow:
    """A window nobody typed, and the sentence that explains where it starts."""

    range: DateRange
    origin: str


def earliest_possible_day(
    expression: PersonExpression, birth_dates: Mapping[str, date | None]
) -> date | None:
    """The first day a picture could satisfy ``expression``, or None when nothing bounds it.

    An AND waits for the last of the people it requires; an OR is open as soon as
    the first of its members exists, and is unbounded when any member's birth date
    is unknown, because that member could have been there all along. A name with no
    birth date contributes no bound of its own.
    """
    if expression.kind == "person":
        assert expression.value is not None
        return birth_dates.get(expression.value)
    bounds = [earliest_possible_day(child, birth_dates) for child in expression.children]
    if expression.kind == "all":
        known = [bound for bound in bounds if bound is not None]
        return max(known) if known else None
    if any(bound is None for bound in bounds):
        return None
    return min(bound for bound in bounds if bound is not None)


def derive_people_window(
    expression: PersonExpression,
    birth_dates: Mapping[str, date | None],
    *,
    today: date,
) -> DerivedPeopleWindow | None:
    """The whole span these people could have been photographed in: that day through today.

    None means no named person has a birth date, so nothing was derived and the
    caller must keep asking for dates rather than inventing a start.
    """
    start = earliest_possible_day(expression, birth_dates)
    if start is None or start > today:
        return None
    return DerivedPeopleWindow(custom_range(start, today), _ORIGIN[expression.kind])


def library_people_window(
    expression: PersonExpression,
    *,
    read_people: Callable[[], Iterable[Person]],
    people_path: Path | None = None,
    today: date | None = None,
) -> DerivedPeopleWindow | None:
    """The window the library's own birth dates allow for a condition nobody dated.

    Immich holds `birthDate` and the curated roster holds the same fact; a name
    known to both keeps the earlier of the two, because the wider window is the
    one that cannot drop a picture that would have matched.
    """
    wanted = set(expression.leaf_values)
    birth_dates: dict[str, date | None] = dict.fromkeys(wanted)
    for name, day in _roster_birth_dates(people_path, wanted):
        _keep_earliest(birth_dates, name, day)
    for person in read_people():
        if person.name in wanted and person.birth_date is not None:
            _keep_earliest(birth_dates, person.name, person.birth_date.date())
    return derive_people_window(expression, birth_dates, today=today or date.today())


def _keep_earliest(birth_dates: dict[str, date | None], name: str, day: date) -> None:
    known = birth_dates.get(name)
    birth_dates[name] = day if known is None else min(known, day)


def _roster_birth_dates(people_path: Path | None, wanted: set[str]) -> Iterator[tuple[str, date]]:
    from immich_memories.people.context import load_people_prompt_context

    for context in load_people_prompt_context(people_path).values():
        if context.name not in wanted or not context.birth_date:
            continue
        with suppress(ValueError):
            yield context.name, date.fromisoformat(context.birth_date)
