"""The polish keeps a film's variety: texture is not voted out, and refills bring what is missing.

Measured 09-24: the 2024 year's vote removed a road race and a group of cyclists, each the only
shot of its kind in its story, and the lifetime film's refills leaned on posed portraits.
"""

from __future__ import annotations

from datetime import timedelta

from immich_memories.analysis.editorial_shot_kinds import PORTRAIT, TEXTURE, shot_kind
from tests.editorial_thin_fixtures import JUNK, START, Film, polish


def test_a_shot_kind_is_read_off_the_frame_people_and_activity_heads():
    assert shot_kind({"frame_kind": "place_or_scenery", "location": "outdoor"}) == TEXTURE
    assert shot_kind({"frame_kind": "people_moment", "people": "crowd"}) == TEXTURE
    assert (
        shot_kind({"frame_kind": "people_moment", "people": "one", "activity": "sport-active"})
        == TEXTURE
    )
    assert (
        shot_kind({"frame_kind": "people_moment", "people": "two", "activity": "posing"})
        == PORTRAIT
    )
    # an empty interior is not a place a film is about: store shelves, a hallway
    assert (
        shot_kind({"frame_kind": "place_or_scenery", "location": "indoor", "people": "one"}) is None
    )
    assert shot_kind({"frame_kind": "place_or_scenery", "location": "outdoor"}) == TEXTURE
    assert (
        shot_kind({"frame_kind": "place_or_scenery", "location": "indoor", "people": "crowd"})
        == TEXTURE
    )
    assert shot_kind({"frame_kind": "lone_everyday_object"}) is None
    assert shot_kind({}) is None


def test_the_vote_does_not_remove_a_storys_only_texture_shot(tmp_path):
    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("p1", "S001", START, "two people smiling"))
    film.draft.append(film.shot("p2", "S001", START + timedelta(hours=2), "two people again"))
    film.draft.append(film.shot("race", "S001", START + timedelta(hours=4), JUNK))
    kinds = {"p1": PORTRAIT, "p2": PORTRAIT, "race": TEXTURE}

    _judge, record, cut, _newcomers = polish(tmp_path, film, kind_of=kinds.get)

    assert "race" in {row["asset_id"] for row in cut}
    verdict = record["verdicts"]["race"]
    # held, so the second order is never asked about it
    assert verdict["state"] == "kept" and verdict["named_by"] >= 1
    assert "not a portrait" in verdict["held_by"]


def test_a_refill_is_offered_the_kind_of_shot_its_story_lacks_first(tmp_path):
    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("p1", "S001", START, "two people smiling"))
    film.draft.append(film.shot("p2", "S001", START + timedelta(days=1), JUNK))
    film.shot("posed", "S001", START + timedelta(hours=3), "two people posing")
    film.shot("square", "S001", START + timedelta(hours=5), "the market square")
    kinds = {"p1": PORTRAIT, "p2": PORTRAIT, "posed": PORTRAIT, "square": TEXTURE}

    _judge, record, _cut, newcomers = polish(tmp_path, film, kind_of=kinds.get)

    assert newcomers == ["square"]
    assert record["shot_kinds"] == {
        "draft": {PORTRAIT: 2},
        "polished": {PORTRAIT: 1, TEXTURE: 1},
    }


def test_variety_never_lifts_a_picture_nothing_vouches_for(tmp_path):
    """The 2024 year (09-25): a story short of texture had a lawnmower on a patio lifted to the
    top of its page, and the picker took it. Variety reorders only what the library vouches for
    (a star, a video, a known person)."""
    film = Film()
    film.tiers["S001"] = "maybe"
    film.draft.append(film.shot("p1", "S001", START, "two people smiling"))
    film.draft.append(film.shot("p2", "S001", START + timedelta(days=1), JUNK))
    film.shot("posed", "S001", START + timedelta(hours=3), "two people posing")
    film.shot("lawn", "S001", START + timedelta(hours=5), "a lawnmower on a patio")
    kinds = {"p1": PORTRAIT, "p2": PORTRAIT, "posed": PORTRAIT, "lawn": TEXTURE}

    _judge, _record, _cut, newcomers = polish(
        tmp_path, film, kind_of=kinds.get, vouched=lambda row: row["asset_id"] != "lawn"
    )

    assert newcomers == ["posed"]
