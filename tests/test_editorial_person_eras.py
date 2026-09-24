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


def _planned_lifetime(tmp_path):
    """A person film over five years, the rules reading it. The last year is full of starred
    days; one quiet day two years earlier holds her too, with nothing starred."""
    from dataclasses import replace

    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.editorial_film_fixtures import film_source, home_days

    busy = [
        replace(day, moments=3)
        for day in home_days(date(2024, 3, 2), 24, step=7, activity="Park day")
    ]
    quiet = home_days(date(2022, 6, 11), 1, activity="Garden afternoon")
    source = film_source(
        tmp_path,
        [*quiet, *busy],
        seconds=40,
        span=(date(2020, 1, 1), date(2024, 12, 31)),
        product="person_spotlight",
        pictures=3,
    )
    for asset_id, asset in source.assets.items():
        asset.is_favorite = asset.file_created_at.year == 2024 and asset_id.endswith("-p0")
    plan = plan_structure(
        source,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
        ),
    ).plan
    return [source.assets[c["asset_id"]].file_created_at.year for c in plan["carriers"]]


def test_a_year_that_holds_the_person_has_a_voice_in_her_film(tmp_path):
    years = _planned_lifetime(tmp_path)

    assert 2022 in years
    assert years.count(2024) >= len(years) - 1
