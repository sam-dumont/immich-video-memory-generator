"""The People page's roster paging and its write discipline (#824, S7)."""

from __future__ import annotations

from pathlib import Path

from immich_memories.people.editor import PersonView
from immich_memories.ui.pages.settings_people import (
    ROSTER_PAGE_SIZE,
    roster_page,
    settle,
)


def _person(index: int) -> PersonView:
    return PersonView(
        person_id=f"person-{index:02d}",
        name=f"Person {index:02d}",
        birth_date=None,
        tier="recurring",
        count=100 - index,
        counts_reliable=True,
        evidence="100 pictures over 12 months",
    )


def test_the_roster_is_cut_into_pages_with_an_honest_label():
    people = [_person(i) for i in range(34)]

    first, label = roster_page(people, 0)
    last, last_label = roster_page(people, 1)

    assert len(first) == ROSTER_PAGE_SIZE == 20
    assert label == "Showing 1–20 of 34"
    assert [p.person_id for p in last] == [p.person_id for p in people[20:]]
    assert last_label == "Showing 21–34 of 34"


def test_a_page_past_the_end_clamps_to_the_last_one():
    people = [_person(i) for i in range(5)]

    shown, label = roster_page(people, 7)

    assert len(shown) == 5
    assert label == "Showing 1–5 of 5"


def test_settle_writes_once_per_real_change(tmp_path: Path):
    person = _person(1)
    writes: list[tuple[str | None, str | None]] = []

    def save(_path: Path, view: PersonView) -> None:
        writes.append((view.role, view.notes))

    assert settle(tmp_path / "people.yaml", person, notes="Grand", save=save) is True
    assert settle(tmp_path / "people.yaml", person, notes="Grand", save=save) is False
    assert settle(tmp_path / "people.yaml", person, notes="Grandma", save=save) is True
    assert settle(tmp_path / "people.yaml", person, role="aunt", save=save) is True
    assert settle(tmp_path / "people.yaml", person, role="aunt", save=save) is False

    assert writes == [(None, "Grand"), (None, "Grandma"), ("aunt", "Grandma")]
