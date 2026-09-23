"""A person film over years opens on the person, not on "Your Year with" or a birth month."""

from datetime import date

from immich_memories.titles.text_builder import generate_title, infer_selection_type


def title_of(start, end, *, person_name="Riley", memory_type="person_spotlight"):
    kind = infer_selection_type(start_date=start, end_date=end, memory_type=memory_type)
    return generate_title(kind, start_date=start, end_date=end, person_name=person_name)


def test_a_person_film_from_a_birth_date_to_today_is_titled_by_the_person():
    title = title_of(date(1989, 12, 3), date(2026, 9, 23))

    assert title.main_title == "Riley"
    assert "Year" not in (title.subtitle or "")
    assert "1989" not in f"{title.main_title} {title.subtitle}"


def test_a_person_film_of_one_calendar_year_keeps_the_year_title():
    title = title_of(date(2024, 1, 1), date(2024, 12, 31))

    assert title.main_title == "2024"
    assert title.subtitle == "Riley"


def test_a_person_film_over_years_with_no_name_falls_back_to_its_dates():
    title = title_of(date(2020, 3, 1), date(2023, 5, 31), person_name=None)

    assert "2020" in title.main_title


def test_a_date_range_that_is_not_a_person_film_is_unchanged():
    title = title_of(date(2020, 3, 1), date(2023, 5, 31), memory_type="year_in_review")

    assert title.subtitle == "Riley"
    assert "2020" in title.main_title
