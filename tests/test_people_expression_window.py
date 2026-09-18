"""The earliest day a people condition can be satisfied, from the people's birth dates."""

from __future__ import annotations

from datetime import date

import yaml

from immich_memories.api.models import Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.people.expression_window import (
    derive_people_window,
    earliest_possible_day,
    library_people_window,
)

ELDER = date(1960, 5, 4)
CHILD = date(2024, 3, 11)


def test_everyone_together_starts_when_the_youngest_was_born():
    """An AND needs every named person in the frame, so it waits for the last of them."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    day = earliest_possible_day(expression, {"Adult A": ELDER, "Child": CHILD})

    assert day == CHILD


def test_either_of_them_starts_when_the_oldest_was_born():
    """An OR is satisfied by whoever is in the frame, so it opens with the first of them."""
    expression = PersonExpression.parse('"Adult A" OR "Child"')

    day = earliest_possible_day(expression, {"Adult A": ELDER, "Child": CHILD})

    assert day == ELDER


def test_a_group_of_adults_with_the_child_waits_for_the_child():
    """(A OR B) AND C still needs C, and C is the youngest of the three."""
    expression = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')

    day = earliest_possible_day(
        expression, {"Adult A": ELDER, "Adult B": date(1958, 1, 2), "Child": CHILD}
    )

    assert day == CHILD


def test_an_unknown_birth_date_leaves_the_bound_to_the_others():
    """A name Immich holds no birth date for says nothing about when the film can start."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    day = earliest_possible_day(expression, {"Adult A": None, "Child": CHILD})

    assert day == CHILD


def test_an_unknown_birth_date_inside_an_or_unbounds_that_group():
    """Whoever that is could have been in frame all along, so the group claims no start."""
    expression = PersonExpression.parse('"Adult A" OR "Child"')

    assert earliest_possible_day(expression, {"Adult A": None, "Child": CHILD}) is None


def test_an_unbounded_group_still_leaves_the_required_child_bounding_the_film():
    """The AND keeps the bound it does know, whatever the OR beside it cannot say."""
    expression = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')

    day = earliest_possible_day(expression, {"Adult A": None, "Adult B": ELDER, "Child": CHILD})

    assert day == CHILD


def test_no_birth_date_anywhere_bounds_nothing():
    """Nothing is known, so nothing is claimed."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    assert earliest_possible_day(expression, {}) is None


def test_forever_runs_from_the_derived_day_to_today():
    """Forever, for these people, is the span in which they could all be photographed."""
    expression = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')

    window = derive_people_window(
        expression,
        {"Adult A": ELDER, "Adult B": ELDER, "Child": CHILD},
        today=date(2026, 9, 18),
    )

    assert window is not None
    assert window.range.start.date() == CHILD
    assert window.range.end.date() == date(2026, 9, 18)


def test_a_derived_window_says_what_bounded_it_without_naming_anyone():
    """The run record and the log explain the start; neither may carry a name."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    window = derive_people_window(
        expression, {"Adult A": ELDER, "Child": CHILD}, today=date(2026, 9, 18)
    )

    assert window is not None
    assert window.origin == (
        "the window starts at the youngest birth date the people it needs allow"
    )
    assert "Child" not in window.origin


def test_nothing_known_derives_no_window():
    """With no birth date anywhere there is nothing to derive, and the caller must ask."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    assert derive_people_window(expression, {}, today=date(2026, 9, 18)) is None


def test_the_library_answers_the_window_from_its_own_birth_dates(tmp_path):
    """Immich holds the birth dates, so the ask needs no invented start."""
    expression = PersonExpression.parse('("Adult A" OR "Adult B") AND "Child"')
    people = [
        Person(id="a", name="Adult A", birthDate="1960-05-04"),
        Person(id="b", name="Adult B", birthDate="1958-01-02"),
        Person(id="c", name="Child", birthDate="2024-03-11"),
    ]

    window = library_people_window(
        expression,
        # WHY: the Immich people listing is the only network read this needs.
        read_people=lambda: people,
        people_path=tmp_path / "absent.yaml",
        today=date(2026, 9, 18),
    )

    assert window is not None
    assert window.range.start.date() == CHILD


def test_the_people_file_answers_for_a_name_immich_has_no_birth_date_for(tmp_path):
    """The curated roster carries the same fact, so a gap in Immich is not a dead end."""
    path = tmp_path / "people.yaml"
    path.write_text(
        yaml.safe_dump({"people": [{"ids": ["c"], "name": "Child", "birth_date": "2024-03-11"}]})
    )
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    window = library_people_window(
        expression,
        # WHY: the Immich people listing is the only network read this needs.
        read_people=lambda: [Person(id="a", name="Adult A", birthDate="1960-05-04")],
        people_path=path,
        today=date(2026, 9, 18),
    )

    assert window is not None
    assert window.range.start.date() == CHILD


def test_a_library_that_knows_no_birth_date_derives_nothing(tmp_path):
    """Nothing known means the caller still has to be told to name its dates."""
    expression = PersonExpression.parse('"Adult A" AND "Child"')

    window = library_people_window(
        expression,
        # WHY: the Immich people listing is the only network read this needs.
        read_people=lambda: [Person(id="a", name="Adult A"), Person(id="c", name="Child")],
        people_path=tmp_path / "absent.yaml",
        today=date(2026, 9, 18),
    )

    assert window is None
