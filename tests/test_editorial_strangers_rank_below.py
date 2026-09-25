"""A moment of strangers only ranks below a moment of the story that shows someone the library knows.

The draft reads a story's moments without a model. When it has room for one of them, a frame of
people Immich knows nobody in (the crowd at a race, passers-by in a square) is the weaker
choice against a frame of the same story that shows a person the owner named. A library that
names nobody keeps its order: strangers are then all the people it has.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.api.models import Person
from tests.editorial_film_fixtures import SEASIDE, Day, film_source

KNOWN_MOMENT = "m3"


def _month(tmp_path, *, names_anyone: bool):
    """Four days away of seven moments with people in every frame; one moment of the first day
    shows a person the library knows, when it names anyone."""
    days = [
        Day(date(2030, 3, 3 + 4 * n), f"A day out {n + 1}", SEASIDE, moments=7) for n in range(4)
    ]
    source = film_source(tmp_path, days, seconds=30, span=(date(2030, 3, 1), date(2030, 3, 31)))
    for asset_id, row in list(source.audience_annotations.items()):
        heads = (*row.heads, ("frame_kind", "people_moment"), ("people", "small-group"))
        source.audience_annotations[asset_id] = replace(row, heads=heads)
        if names_anyone and asset_id.startswith(f"d000-{KNOWN_MOMENT}-"):
            source.assets[asset_id].people = [Person(id="person-robin", name="Robin")]
    plan = plan_structure(
        source,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
        ),
    ).plan
    return [c["asset_id"] for c in plan["carriers"] if c["asset_id"].startswith("d000-")]


def test_a_known_person_s_moment_is_picked_before_the_strangers_of_its_story(tmp_path):
    first_day = _month(tmp_path, names_anyone=True)

    assert first_day, "the first day must hold a shot for the case to be asked"
    assert any(a.startswith(f"d000-{KNOWN_MOMENT}-") for a in first_day)


def test_a_library_that_names_nobody_keeps_its_order(tmp_path):
    named = _month(tmp_path / "named", names_anyone=True)
    unnamed = _month(tmp_path / "unnamed", names_anyone=False)

    assert not any(a.startswith(f"d000-{KNOWN_MOMENT}-") for a in unnamed)
    assert len(unnamed) == len(named)
