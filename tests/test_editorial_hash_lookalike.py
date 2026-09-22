"""The no-model look-alike: the perceptual hash the pipeline already caches."""

import pytest

from immich_memories.analysis.editorial_story_lookalike import (
    LOOK_ALIKE_HASH_DISTANCE,
    LookAlikeCheck,
    hash_pair_relation,
)

HASHES = {
    "a": "ffffffffffffffff",
    "twin": "ffffffffffffff7f",  # one bit from "a"
    "other": "0000ffff0000ffff",
    "blank": "",
}


def _carrier(asset_id, *, story="S001", taken="2024-06-04T10:00:00", favourite=False):
    return {
        "asset_id": asset_id,
        "story_episode": story,
        "taken": taken,
        "favourite": favourite,
        "kind": "still",
    }


def _looks_alike():
    return hash_pair_relation(HASHES.get)


def test_two_near_identical_frames_of_one_story_repeat_each_other():
    assert _looks_alike()(_carrier("twin"), _carrier("a")) is True


def test_two_different_frames_of_one_story_do_not():
    assert _looks_alike()(_carrier("other"), _carrier("a")) is False


def test_the_same_frame_on_another_day_of_another_story_is_not_compared():
    candidate = _carrier("twin", story="S002", taken="2024-07-01T10:00:00")
    assert _looks_alike()(candidate, _carrier("a")) is False


def test_the_same_day_is_compared_even_across_stories():
    assert _looks_alike()(_carrier("twin", story="S002"), _carrier("a")) is True


def test_a_frame_with_no_cached_hash_is_unknown_rather_than_distinct():
    assert _looks_alike()(_carrier("blank"), _carrier("a")) is None
    assert _looks_alike()(_carrier("missing"), _carrier("a")) is None


def test_a_favourite_is_never_refused_for_looking_like_an_unstarred_frame():
    check = LookAlikeCheck(_looks_alike(), slots=10)
    assert check.repeats(_carrier("twin", favourite=True), [_carrier("a")]) is None
    assert check.repeats(_carrier("twin"), [_carrier("a", favourite=True)]) == "a"


def test_a_frame_that_plays_is_not_refused_for_looking_like_a_still():
    """A video product prefers motion; a still never displaces the clip of its moment."""
    playing = _carrier("twin") | {"kind": "video"}
    assert _looks_alike()(playing, _carrier("a")) is False
    assert _looks_alike()(_carrier("twin"), _carrier("a") | {"kind": "video"}) is True


def test_the_threshold_is_the_production_corroboration_distance():
    assert LOOK_ALIKE_HASH_DISTANCE == 10


@pytest.mark.parametrize("distance", [0, 10])
def test_the_check_reports_itself_as_asked_rather_than_unavailable(distance):
    check = LookAlikeCheck(hash_pair_relation(HASHES.get, distance=distance), slots=4)
    check.repeats(_carrier("twin"), [_carrier("a")])
    assert check.record()["status"] == "asked"
    assert check.record()["checks"] == 1
