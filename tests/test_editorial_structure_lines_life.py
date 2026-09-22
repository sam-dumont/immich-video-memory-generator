"""Without a sentence, "shows life" is read from Immich's people and the people head."""

from types import SimpleNamespace

from immich_memories.analysis.editorial_structure_lines import UnitLines, metadata_life

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
    """The model reader is handed no fallback, so its judgement is unchanged."""
    assert UnitLines(BARE).shows_life(STILL) is False
    assert UnitLines(BARE).shows_life(STILL | {"favourite": True}) is True
    assert UnitLines(BARE).shows_life(STILL | {"kind": "video"}) is True


def test_a_video_or_a_favourite_still_shows_life_whatever_the_metadata_says():
    life = metadata_life({"a": _asset()}, {"a": _heads(people="none")})
    lines = UnitLines(BARE, life_without_prose=life)
    assert lines.shows_life(STILL | {"kind": "video"}) is True
    assert lines.shows_life(STILL | {"favourite": True}) is True
