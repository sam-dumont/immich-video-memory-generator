"""Heavy scopes narrow before descriptions without pretending metadata has taste."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from probe_description_allocation import build_description_workprint

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_structure import StructureMoment, StructureWorkprint
from immich_memories.api.models import Person
from tests.conftest import make_asset

START = datetime(2020, 1, 1, tzinfo=UTC)


def _moment(
    index: int,
    *,
    year: int = 2020,
    favourite: bool = False,
    people: tuple[str, ...] = (),
) -> StructureMoment:
    asset = make_asset(
        f"asset-{index}",
        file_created_at=START.replace(year=year) + timedelta(days=index),
    )
    asset.is_favorite = favourite
    asset.people = [Person(id=f"person-{name}", name=name) for name in people]
    candidate = EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=favourite,
        source=asset,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )
    return StructureMoment(f"moment-{index}", (candidate,), candidate)


def _allocate(*moments: StructureMoment, relationships: tuple[str, ...] = ()):
    return build_description_workprint(
        StructureWorkprint(tuple(moments)),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        relationship_names=relationships,
        reduce_above_moments=0,
    )


def test_every_favourite_moment_enters_without_collapsing_same_chapter_favourites() -> None:
    first = _moment(1, favourite=True)
    second = _moment(2, favourite=True)

    allocated = _allocate(first, second)

    assert allocated.moments == (first, second)


def test_a_chapter_with_no_favourites_stays_complete() -> None:
    old = (_moment(1, year=2010), _moment(2, year=2010))
    recent = (_moment(3, year=2020, favourite=True), _moment(4, year=2020))

    allocated = _allocate(*old, *recent)

    assert {moment.moment_id for moment in allocated.moments} == {
        old[0].moment_id,
        old[1].moment_id,
        recent[0].moment_id,
    }


def test_missing_relationship_context_gets_first_and_last_views_not_every_view() -> None:
    favourite = _moment(1, favourite=True, people=("Casey",))
    with_partner = tuple(_moment(index, people=("Morgan",)) for index in range(2, 6))

    allocated = _allocate(favourite, *with_partner, relationships=("Morgan",))

    assert allocated.moments == (favourite, with_partner[0], with_partner[-1])


def test_a_persons_first_appearance_and_return_after_two_years_enter() -> None:
    first = _moment(1, year=2010, people=("Seb",))
    ordinary = _moment(2, year=2010, people=("Seb",), favourite=True)
    returned = _moment(3, year=2013, people=("Seb",))

    allocated = _allocate(first, ordinary, returned)
    by_id = {item.moment.moment_id: item.reasons for item in allocated.admissions}

    assert "first-copresence:Seb" in by_id[first.moment_id]
    assert "resumption:Seb" in by_id[returned.moment_id]


def test_small_walls_pass_through_without_applying_the_heavy_scope_rules() -> None:
    moments = tuple(_moment(index) for index in range(3))

    allocated = build_description_workprint(
        StructureWorkprint(moments),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        reduce_above_moments=3,
    )

    assert allocated.moments == moments
    assert {item.reasons for item in allocated.admissions} == {("complete-wall",)}
