"""What a people or occasion memory tells the model before it names the film.

A film about a child and her grandparents used to open on a date span with
three full names stacked underneath. The model can write "Ada and her
grandparents" instead, but only if it is told who these people are to each
other. These tests pin the facts that go in, and the story text that must not.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from immich_memories.titles.llm_titles import MemoryTitleFacts, build_title_prompt


def _people_file(tmp_path: Path) -> Path:
    """A grandmother and a grandchild, the relation recorded in both directions."""
    path = tmp_path / "people.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "owner": {"person_id": "person-owner", "name": "Alex Example"},
                "people": [
                    {
                        "ids": ["person-ada"],
                        "name": "Ada Example",
                        "birth_date": "2024-02-07",
                        "confirmed": {
                            "links": [
                                {"kind": "grandchild-of", "with": "person-grace"},
                            ]
                        },
                    },
                    {
                        "ids": ["person-grace"],
                        "name": "Grace Example",
                        "birth_date": "1955-03-11",
                        "confirmed": {
                            "links": [
                                {"kind": "grandparent-of", "with": "person-ada"},
                            ]
                        },
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _people_prompt(tmp_path: Path, *, end: str = "2026-09-17", today: date | None = None) -> str:
    return build_title_prompt(
        memory_type="multi_person",
        locale="fr",
        start_date="2024-02-07",
        end_date=end,
        duration_days=953,
        person_names=["Ada Example", "Grace Example"],
        facts=MemoryTitleFacts(
            people_condition='("Ada Example" AND "Grace Example")',
            people_path=_people_file(tmp_path),
            today=today or date(2026, 9, 17),
        ),
    )


def test_a_two_person_film_carries_the_count_and_both_directions_of_the_pair(tmp_path):
    """One line per ordered pair: the model sees the relation from each side."""
    prompt = _people_prompt(tmp_path)

    assert "People in the film: 2" in prompt
    assert "- Ada Example -> Grace Example: grandchild of (confirmed)" in prompt
    assert "- Grace Example -> Ada Example: grandparent of (confirmed)" in prompt


def test_a_person_the_family_record_does_not_know_says_so_rather_than_vanishing(tmp_path):
    """Immich names the faces; the people file is opt-in and may not have them."""
    prompt = build_title_prompt(
        memory_type="multi_person",
        locale="fr",
        start_date="2024-02-07",
        end_date="2026-09-17",
        duration_days=953,
        person_names=["Ada Example", "Robin Example"],
        facts=MemoryTitleFacts(people_path=_people_file(tmp_path), today=date(2026, 9, 17)),
    )

    assert "- Robin Example: birth date unknown" in prompt
    assert "- Ada Example -> Robin Example: no recorded relation" in prompt


def test_the_span_says_what_it_is_rather_than_only_where_it_ends(tmp_path):
    """A whole life so far is a fact about the subject, not a date range."""
    prompt = _people_prompt(tmp_path)

    assert "starts on Ada Example's birth date" in prompt
    assert "ends today (open-ended)" in prompt


def test_a_span_that_stops_short_of_today_is_not_called_open_ended(tmp_path):
    prompt = _people_prompt(tmp_path, end="2025-06-30", today=date(2026, 9, 17))

    assert "ends today (open-ended)" not in prompt


def test_a_calendar_year_is_named_as_one(tmp_path):
    prompt = build_title_prompt(
        memory_type="person_spotlight",
        locale="fr",
        start_date="2025-01-01",
        end_date="2025-12-31",
        duration_days=364,
        person_names=["Ada Example"],
        facts=MemoryTitleFacts(people_path=_people_file(tmp_path), today=date(2026, 9, 17)),
    )

    assert "the calendar year 2025" in prompt
    assert 'People condition (every picture satisfies it): "Ada Example"' in prompt


def test_the_films_own_reading_of_the_period_never_reaches_the_title_prompt(tmp_path):
    """The readings carry names promoted from banners; a title may not invent."""
    prompt = build_title_prompt(
        memory_type="multi_person",
        locale="fr",
        start_date="2024-02-07",
        end_date="2026-09-17",
        duration_days=953,
        person_names=["Ada Example", "Grace Example"],
        clip_descriptions=["the Example Festival main stage at dusk"],
        smart_objects=["stage", "banner"],
        facts=MemoryTitleFacts(people_path=_people_file(tmp_path), today=date(2026, 9, 17)),
    )

    assert "Example Festival" not in prompt
    assert "banner" not in prompt


def test_a_special_day_is_told_what_the_catalogue_called_it(tmp_path):
    prompt = build_title_prompt(
        memory_type="special_day",
        locale="fr",
        start_date="2025-12-21",
        end_date="2025-12-21",
        duration_days=0,
        person_names=["Ada Example"],
        daily_locations=["2025-12-21: Brussels(6)"],
        facts=MemoryTitleFacts(
            occasion_name="an afternoon at a themed bowling alley",
            people_path=_people_file(tmp_path),
            today=date(2026, 9, 17),
        ),
    )

    assert "an afternoon at a themed bowling alley" in prompt
    assert "2025-12-21: Brussels(6)" in prompt
    assert "Chypre" in prompt  # the place-name-language rule travels with it


def test_an_album_memory_is_told_the_name_somebody_typed(tmp_path):
    prompt = build_title_prompt(
        memory_type="album",
        locale="en",
        start_date="2022-05-01",
        end_date="2022-05-03",
        duration_days=2,
        facts=MemoryTitleFacts(album_name="Old Negatives 75"),
    )

    assert "Old Negatives 75" in prompt


def test_a_trip_keeps_its_own_prompt(tmp_path):
    prompt = build_title_prompt(
        memory_type="trip",
        locale="en",
        start_date="2024-08-26",
        end_date="2024-09-04",
        duration_days=9,
        daily_locations=["2024-08-26: Nicosia (35.17, 33.36)"],
        country="Cyprus",
    )

    assert "Trip Pattern Classification" in prompt
    assert "trip_type" in prompt
