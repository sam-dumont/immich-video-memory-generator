"""Without a sentence, "shows life" is read from Immich's people and the people head."""

from dataclasses import replace
from types import SimpleNamespace

from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_lines import UnitLines, metadata_life
from immich_memories.analysis.editorial_structure_material import build_material, read_wall
from immich_memories.api.models import Person
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source

STILL = {"asset_id": "a", "kind": "still", "favourite": False}
# the two line shapes a bank actually holds, with and without the caption seat
CAPTIONED = {"a": "2023-06-04 09:00 | at a town | a wide empty beach at dawn | activity=other"}
BARE = {"a": "2023-06-04 09:00 | at a town | activity=other, location=outdoor, people=one"}


def _person():
    return SimpleNamespace(id="p1", name="someone")


def _asset(*, people=(), faces=()):
    return SimpleNamespace(id="a", people=list(people), faces=list(faces))


def _heads(**labels):
    return SimpleNamespace(heads=tuple(labels.items()))


def test_a_captioned_line_is_still_judged_on_its_caption_alone():
    """A caption that names nobody keeps answering no, whatever the metadata says."""
    life = metadata_life({"a": _asset(people=[_person()])}, {"a": _heads(people="crowd")})
    assert UnitLines(CAPTIONED, life_without_prose=life).shows_life(STILL) is False


def test_a_captionless_picture_shows_life_when_immich_names_a_face():
    life = metadata_life({"a": _asset(people=[_person()])}, {})
    assert UnitLines(BARE, life_without_prose=life).shows_life(STILL) is True


def test_a_captionless_picture_shows_life_when_the_people_head_saw_somebody():
    life = metadata_life({"a": _asset()}, {"a": _heads(people="small-group")})
    assert UnitLines(BARE, life_without_prose=life).shows_life(STILL) is True


def test_a_captionless_picture_of_nobody_still_shows_no_life():
    life = metadata_life({"a": _asset()}, {"a": _heads(people="none")})
    assert UnitLines(BARE, life_without_prose=life).shows_life(STILL) is False
    undetermined = metadata_life({"a": _asset()}, {"a": _heads(people="undetermined")})
    assert UnitLines(BARE, life_without_prose=undetermined).shows_life(STILL) is False


def test_without_the_reading_a_captionless_still_answers_as_it_always_did():
    """A text-only reading with no facts beside it has nothing else to answer from."""
    assert UnitLines(BARE).shows_life(STILL) is False
    assert UnitLines(BARE).shows_life(STILL | {"favourite": True}) is True
    assert UnitLines(BARE).shows_life(STILL | {"kind": "video"}) is True


def test_a_video_or_a_favourite_still_shows_life_whatever_the_metadata_says():
    life = metadata_life({"a": _asset()}, {"a": _heads(people="none")})
    lines = UnitLines(BARE, life_without_prose=life)
    assert lines.shows_life(STILL | {"kind": "video"}) is True
    assert lines.shows_life(STILL | {"favourite": True}) is True


def test_a_model_tier_picture_the_captioner_left_bare_still_shows_its_named_face(tmp_path):
    """Nothing about reading the facts beside a blank line needs the no-model reader."""
    captured = source(tmp_path, seconds=12, pictures=2)
    named = {
        asset_id: asset.model_copy(update={"people": [Person(id="p1", name="Someone")]})
        for asset_id, asset in captured.assets.items()
    }
    bare = {
        asset_id: f"{asset.file_created_at.isoformat()} | activity=other"
        for asset_id, asset in named.items()
    }
    captured = replace(captured, assets=named, annotations=bare)
    ports = StructurePlannerPorts(
        judge=ControlledStoryJudge(),
        thumbnail_hash=lambda _: None,
    )

    material = build_material(captured, ports, read_wall(captured))

    assert ports.rules is None, "a model tier, with no rules reader anywhere"
    assert all(material.text.shows_life(u) for units in material.units.values() for u in units)


def test_only_a_named_visible_person_is_someone_the_library_knows():
    from immich_memories.analysis.editorial_structure_lines import strangers_only

    named = SimpleNamespace(id="p1", name="someone", is_hidden=False)
    unnamed = SimpleNamespace(id="p2", name="", is_hidden=False)
    hidden = SimpleNamespace(id="p3", name="someone else", is_hidden=True)
    assets = {
        "known": SimpleNamespace(id="known", people=[named], faces=[]),
        "unnamed": SimpleNamespace(id="unnamed", people=[unnamed], faces=[]),
        "hidden": SimpleNamespace(id="hidden", people=[hidden], faces=[]),
        "crowd": SimpleNamespace(id="crowd", people=[], faces=[]),
        "view": SimpleNamespace(id="view", people=[], faces=[]),
    }
    heads = {"crowd": _heads(people="crowd"), "view": _heads(people="none")}

    stranger = strangers_only(assets, heads)

    assert [a for a in assets if stranger(a)] == ["unnamed", "hidden", "crowd"]
    no_names = {k: v for k, v in assets.items() if k != "known"}
    assert not any(strangers_only(no_names, heads)(a) for a in no_names)
