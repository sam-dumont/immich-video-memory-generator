"""A person film over many years speaks of the years that hold her, not every year since birth."""

from datetime import UTC, date, datetime

from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.timeperiod import DateRange

LIFETIME = (
    DateRange(datetime(1989, 12, 3, tzinfo=UTC), datetime(2026, 9, 23, 23, 59, tzinfo=UTC)),
)


def test_a_year_with_no_picture_of_the_person_is_no_partition():
    material = {date(2008, 5, 1), date(2015, 7, 14), date(2024, 1, 2)}

    intent = build_editorial_intent(
        "person_spotlight", LIFETIME, brief="", people=("Person",), material=material
    )

    assert [p.key for p in intent.partitions] == ["year-2008", "year-2015", "year-2024"]
    assert "1995" not in intent.prompt_block()


def test_without_the_material_every_year_stays_a_partition():
    intent = build_editorial_intent("person_spotlight", LIFETIME, brief="", people=("Person",))

    assert len(intent.partitions) == 2026 - 1989 + 1


def test_a_year_film_keeps_its_one_partition_whatever_the_material():
    year = (DateRange(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 12, 31, tzinfo=UTC)),)

    intent = build_editorial_intent(
        "person_spotlight", year, brief="", people=("Person",), material={date(2024, 3, 1)}
    )

    assert [p.key for p in intent.partitions] == ["scope"]
