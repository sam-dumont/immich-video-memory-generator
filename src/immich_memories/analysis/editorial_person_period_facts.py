"""Dated people evidence kept apart from bounded scene descriptions."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime

from immich_memories.analysis import editorial_wall_rows as wall


@dataclass(frozen=True, slots=True)
class PersonPeriodFact:
    """A person's recorded appearance in this family, not a relationship start date."""

    person_token: str
    name: str
    current_relationship: str | None
    relationship_source: str | None
    tier: str | None
    first_library_month: str | None
    sustained_onset_month: str | None
    grounding_moment_ids: tuple[str, ...]


def _month(value: str | None) -> str | None:
    if not value or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        return None
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError:
        return None
    return value


def _taken_month(value: str | None) -> str | None:
    try:
        taken = datetime.fromisoformat(value or "")
    except ValueError:
        return None
    return f"{taken.year:04d}-{taken.month:02d}"


def _people_and_moments(tables) -> tuple[dict, dict]:
    people_rows = wall._records(tables, "people")
    if any(set(row) != wall.PEOPLE_FIELDS for row in people_rows):
        raise ValueError("direct moment people fields differ from the sealed contract")
    return (
        wall._unique_index(people_rows, "id", "people"),
        wall._unique_index(wall._records(tables, "moments"), "id", "moments"),
    )


def _associations(tables, moments) -> dict:
    associations = wall._by_moment(wall._records(tables, "moment_people"), "moment_people")
    if associations.keys() - moments.keys():
        raise ValueError("person period association references an unknown moment")
    return associations


def _matched_on_a_dated_month(moment_id, month, associations, people) -> list[str]:
    """The tagged people whose first or sustained-onset month is this moment's month."""
    tokens = []
    for association in associations.get(moment_id, ()):
        token = association.get("person", "")
        # The established factual-wall projection omits unnamed U identities.
        if not re.fullmatch(r"P[0-9]+", token):
            continue
        person = people.get(token)
        if person is None:
            raise ValueError("direct moment person association is ungrounded")
        if month and month in {_month(person.get("first")), _month(person.get("onset"))}:
            tokens.append(token)
    return tokens


def _period_fact(token, person, grounding, moments) -> PersonPeriodFact:
    months = {_taken_month(moments[m].get("taken")) for m in grounding}
    first, onset = _month(person.get("first")), _month(person.get("onset"))
    return PersonPeriodFact(
        person_token=token,
        name=person["name"],
        current_relationship=wall._optional(person.get("relationship")),
        relationship_source=wall._optional(person.get("source")),
        tier=wall._optional(person.get("tier")),
        first_library_month=first if first in months else None,
        sustained_onset_month=onset if onset in months else None,
        grounding_moment_ids=tuple(sorted(grounding)),
    )


def person_period_facts(
    tables: dict[str, tuple[list[str], list[list[str]]]],
    moment_ids: Sequence[str],
) -> tuple[PersonPeriodFact, ...]:
    """Project only first/onset months grounded by actual tagged family members.

    Current graph roles and inferred library dates remain distinct facts. No person
    receives admission, funding or a picture requirement from this projection.
    """
    people, moments = _people_and_moments(tables)
    selected = set(moment_ids)
    if selected - moments.keys():
        raise ValueError("person period facts reference an unknown moment")
    associations = _associations(tables, moments)
    matched: dict[str, set[str]] = {}
    for moment_id in selected:
        month = _taken_month(moments[moment_id].get("taken"))
        for token in _matched_on_a_dated_month(moment_id, month, associations, people):
            matched.setdefault(token, set()).add(moment_id)
    return tuple(
        _period_fact(token, people[token], grounding, moments)
        for token, grounding in sorted(matched.items())
    )


def render_person_period_facts(facts: Sequence[PersonPeriodFact]) -> str:
    """A stable independent field that scene-prose shortening cannot truncate."""
    if not facts:
        return ""
    return json.dumps([asdict(fact) for fact in facts], ensure_ascii=False, separators=(",", ":"))
