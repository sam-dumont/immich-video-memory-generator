"""Every cut ends with the same duplicate review, whatever reader made it."""

from immich_memories.analysis.editorial_final_hash_review import (
    POLICY,
    STANDALONE_REPEAT_DISTANCE,
    review_cut_by_cached_hashes,
)

HASHES = {
    "early": "ffffffffffffffff",
    "late": "ffffffffffffff7f",  # one bit from "early"
    "faint": "ffffffffffffff00",  # eight bits from "early": alike to the old cap, not to this one
    "twin": "ffffffffffffff3f",  # two bits from "early"
    "far": "ffffffffffff0000",  # sixteen bits from "early"
    "other": "0000ffff0000ffff",
}


def _carrier(asset_id, *, minute, favourite=False, kind="still", day=4, story="story-1"):
    return {
        "asset_id": asset_id,
        "taken": f"2024-06-{day:02}T10:{minute:02}:00",
        "favourite": favourite,
        "kind": kind,
        "story_episode": story,
    }


def _review(carriers, **kwargs):
    return review_cut_by_cached_hashes(carriers, thumbnail_hash=HASHES.get, **kwargs)


def test_a_repeat_of_a_frame_the_cut_already_holds_is_dropped():
    survivors, record = _review([_carrier("early", minute=0), _carrier("late", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early"}]
    assert record["policy"] == POLICY
    assert record["maximum_hash_distance"] == STANDALONE_REPEAT_DISTANCE


def test_two_frames_of_different_things_both_stay():
    survivors, record = _review([_carrier("early", minute=0), _carrier("other", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["removals"] == []


def test_a_pair_past_the_corroboration_distance_is_not_a_repeat():
    survivors, _ = _review([_carrier("early", minute=0), _carrier("far", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "far"]


def test_a_pair_eight_bits_apart_is_not_cut_on_the_hash_alone():
    """Nothing confirms a pair here, so the hash has to be close enough to carry it alone."""
    survivors, record = _review([_carrier("early", minute=0), _carrier("faint", minute=30)])

    assert [c["asset_id"] for c in survivors] == ["early", "faint"]
    assert record["maximum_hash_distance"] == STANDALONE_REPEAT_DISTANCE < 8


def test_two_frames_sharing_neither_a_story_nor_a_day_are_never_a_repeat():
    """Months apart in different stories, an average hash agreeing is a coincidence."""
    survivors, record = _review(
        [
            _carrier("early", minute=0, day=4, story="story-1"),
            _carrier("late", minute=0, day=20, story="story-2"),
        ]
    )

    assert [c["asset_id"] for c in survivors] == ["early", "late"]
    assert record["removals"] == []
    assert record["pairs_compared"] == 0


def test_one_story_is_read_across_its_own_days():
    """A trip is one story: the same shot taken again on its third day is still a repeat."""
    survivors, record = _review(
        [
            _carrier("early", minute=0, day=4, story="trip"),
            _carrier("late", minute=0, day=6, story="trip"),
        ]
    )

    assert [c["asset_id"] for c in survivors] == ["early"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early"}]


def test_one_day_is_read_across_its_stories():
    survivors, _ = _review(
        [
            _carrier("early", minute=0, day=4, story="story-1"),
            _carrier("late", minute=30, day=4, story="story-2"),
        ]
    )

    assert [c["asset_id"] for c in survivors] == ["early"]


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
    """A tick outranks every other rung."""
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


def _unit(asset_id, *, minute, day=4, story="story-1"):
    """What a replacement pool offers: the unit row of a picture the film is not showing."""
    return {
        "asset_id": asset_id,
        "taken": f"2024-06-{day:02}T10:{minute:02}:00",
        "kind": "still",
        "seconds": 4.0,
    }


def test_a_refused_frame_is_replaced_from_its_own_moment():
    """The film loses the repeat, not the moment: nothing else will ever show it."""
    cut = [_carrier("early", minute=0), _carrier("late", minute=30)]
    late_alternatives = {"late": [("moment", _unit("other", minute=31))]}

    survivors, record = _review(
        cut, replacements_for=lambda c: late_alternatives.get(c["asset_id"], [])
    )

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early", "replacement": "other"}]
    assert record["replaced_from"] == {"moment": 1}


def test_a_replacement_that_repeats_the_cut_itself_is_passed_over():
    """Refilling a repeat with a repeat would only hand the problem to the next pass."""
    cut = [_carrier("early", minute=0), _carrier("late", minute=30)]
    offers = [("moment", _unit("twin", minute=31)), ("moment", _unit("other", minute=32))]

    survivors, record = _review(cut, replacements_for=lambda _c: offers)

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["replaced_from"] == {"moment": 1}


def test_the_story_is_asked_only_after_the_moment_has_nothing():
    cut = [_carrier("early", minute=0), _carrier("late", minute=30)]
    offers = [("moment", _unit("late", minute=30)), ("story", _unit("other", minute=45))]

    survivors, record = _review(cut, replacements_for=lambda _c: offers)

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["replaced_from"] == {"story": 1}


def test_a_picture_the_film_already_shows_is_never_offered_twice():
    cut = [_carrier("early", minute=0), _carrier("other", minute=10), _carrier("late", minute=30)]

    survivors, record = _review(
        cut, replacements_for=lambda _c: [("moment", _unit("other", minute=11))]
    )

    assert [c["asset_id"] for c in survivors] == ["early", "other"]
    assert record["replaced_from"] == {}
    assert "replacement" not in record["removals"][0]


def test_a_slot_with_nothing_to_put_in_it_is_the_shortfall_the_film_takes():
    cut = [_carrier("early", minute=0), _carrier("late", minute=30)]

    survivors, record = _review(
        cut, replacements_for=lambda _c: [("moment", _unit("unhashed", minute=31))]
    )

    assert [c["asset_id"] for c in survivors] == ["early"]
    assert record["removals"] == [{"asset_id": "late", "keeper": "early"}]
    assert record["replaced_from"] == {}


def test_a_replacement_takes_its_own_place_in_the_films_order():
    """Always chronological: the replacement sits where its own capture time puts it."""
    cut = [_carrier("early", minute=30), _carrier("late", minute=40)]
    offers = [("moment", _unit("far", minute=0))]

    survivors, _ = _review(cut, replacements_for=lambda _c: offers)

    assert [c["asset_id"] for c in survivors] == ["far", "early"]


def test_a_replacement_keeps_the_slot_story_and_describes_itself():
    cut = [_carrier("early", minute=0), _carrier("late", minute=30, story="trip")]

    survivors, _ = _review(
        cut,
        replacements_for=lambda _c: [("moment", _unit("other", minute=31) | {"line": "its own"})],
    )

    replacement = next(c for c in survivors if c["asset_id"] == "other")
    assert replacement["story_episode"] == "trip"
    assert replacement["line"] == "its own"
    assert replacement["taken"] == "2024-06-04T10:31:00"


def test_a_cut_whose_previews_are_all_cached_is_a_complete_review():
    _, record = _review([_carrier("early", minute=0), _carrier("other", minute=30)])

    assert record["incomplete"] is False
    assert record["input_carriers"] == 2
    assert record["output_carriers"] == 2
    assert record["pairs_compared"] == 1


def test_the_pool_is_offered_as_the_moment_first_and_then_the_story():
    """The audience gate's own pool, labelled: the moment's siblings, then the story's spares."""
    from immich_memories.analysis.editorial_structure_finishing import replacement_offers

    carrier = {"asset_id": "late", "moment_alternatives": ["sibling"]}
    pool = [_unit("sibling", minute=31), _unit("elsewhere", minute=90)]

    offers = replacement_offers(lambda _c: pool)(carrier)

    assert [(rung, unit["asset_id"]) for rung, unit in offers] == [
        ("moment", "sibling"),
        ("story", "elsewhere"),
    ]


def test_a_carrier_the_gate_already_substituted_has_no_moment_to_ask():
    from immich_memories.analysis.editorial_structure_finishing import replacement_offers

    offers = replacement_offers(lambda _c: [_unit("elsewhere", minute=90)])({"asset_id": "swapped"})

    assert [rung for rung, _unit in offers] == ["story"]
