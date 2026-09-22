"""A no-model cut ends with the same shape of duplicate review a model cut gets."""

from immich_memories.analysis.editorial_final_hash_review import (
    POLICY,
    review_cut_by_cached_hashes,
)
from immich_memories.analysis.selection_same_picture import SELECTS_MAX_CORROBORATION

HASHES = {
    "early": "ffffffffffffffff",
    "late": "ffffffffffffff7f",  # one bit from "early"
    "far": "ffffffffffff0000",  # sixteen bits from "early"
    "other": "0000ffff0000ffff",
}


def _carrier(asset_id, *, minute, favourite=False, kind="still"):
    return {
        "asset_id": asset_id,
        "taken": f"2024-06-04T10:{minute:02}:00",
        "favourite": favourite,
        "kind": kind,
    }


def _review(carriers, **kwargs):
    return review_cut_by_cached_hashes(carriers, thumbnail_hash=HASHES.get, **kwargs)


def test_a_repeat_of_a_frame_the_cut_already_holds_is_dropped():
    survivors, record = _review([_carrier("early", minute=0), _carrier("late", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early"}]
    assert record["policy"] == POLICY
    assert record["maximum_hash_distance"] == SELECTS_MAX_CORROBORATION


def test_two_frames_of_different_things_both_stay():
    survivors, record = _review([_carrier("early", minute=0), _carrier("other", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["removals"] == []


def test_a_pair_past_the_corroboration_distance_is_not_a_repeat():
    survivors, _ = _review([_carrier("early", minute=0), _carrier("far", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "far"]


def test_the_whole_cut_is_compared_not_only_neighbours():
    """Two frames of the same thing four shots apart are what the selection check misses."""
    cut = [
        _carrier("early", minute=0),
        _carrier("other", minute=5),
        _carrier("far", minute=10),
        _carrier("late", minute=15),
    ]

    survivors, record = _review(cut)

    assert [c["asset_id"] for c in survivors] == ["early", "other", "far"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early"}]


def test_the_favourite_of_a_look_alike_pair_keeps_its_frame():
    survivors, _ = _review(
        [_carrier("early", minute=0), _carrier("late", minute=30, favourite=True)]
    )

    assert [c["asset_id"] for c in survivors] == ["late"]


def test_a_frame_that_plays_is_kept_over_the_still_that_repeats_it():
    survivors, _ = _review([_carrier("early", minute=0), _carrier("late", minute=30, kind="video")])

    assert [c["asset_id"] for c in survivors] == ["late"]


def test_an_owner_required_picture_keeps_its_frame_and_the_repeat_goes_instead():
    """A tick outranks every other rung, exactly as the sampled review ranks it."""
    survivors, record = _review(
        [_carrier("early", minute=0), _carrier("late", minute=30)],
        protected_asset_ids=("late",),
    )

    assert [c["asset_id"] for c in survivors] == ["late"]
    assert record["removals"] == [{"asset_id": "early", "keeper": "late"}]


def test_the_survivors_stay_in_the_order_the_cut_gave_them():
    cut = [_carrier("other", minute=0), _carrier("early", minute=5), _carrier("late", minute=10)]

    survivors, _ = _review(cut)

    assert [c["asset_id"] for c in survivors] == ["other", "early"]


def test_a_frame_with_no_cached_hash_is_kept_and_the_review_says_it_is_incomplete():
    survivors, record = _review([_carrier("early", minute=0), _carrier("unhashed", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "unhashed"]
    assert record["unavailable"] == ["unhashed"]
    assert record["incomplete"] is True


def test_a_cut_whose_previews_are_all_cached_is_a_complete_review():
    _, record = _review([_carrier("early", minute=0), _carrier("other", minute=30)])

    assert record["incomplete"] is False
    assert record["input_carriers"] == 2
    assert record["output_carriers"] == 2
    assert record["pairs_compared"] == 1
