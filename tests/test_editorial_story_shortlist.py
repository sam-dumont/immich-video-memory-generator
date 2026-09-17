"""Cheap shortlist breadth precedes standing and audience admission, within the same cap."""

from dataclasses import asdict

import pytest

from immich_memories.analysis.editorial_story_shortlist import (
    DepictedChoice,
    shortlist_story_moments,
)


def choices(count=12):
    return [
        DepictedChoice(
            key=str(index),
            episode="story",
            taken=f"2030-05-{index:02d}T10:00:00",
            content="A moment",
            primary=f"asset-{index}",
        )
        for index in range(1, count + 1)
    ]


def shortlist(pool, *, grant=1, stars=(), lively=None, relations=None):
    return shortlist_story_moments(
        pool,
        grant,
        starred=lambda c: int(c.key) in stars,
        life=lambda asset: lively is None or int(asset.split("-")[-1]) in lively,
        kind_of=lambda c: f" | with: {relations.get(int(c.key), '')}" if relations else "",
    )


def keys(selected):
    return [int(c.key) for c in selected]


def test_uncapped_input_is_returned_untouched_without_assessing_it():
    pool = list(reversed(choices(6)))

    def unnecessary_assessment(_value):
        raise AssertionError("An uncapped shortlist needs no preference assessment")

    selected = shortlist_story_moments(
        pool,
        1,
        starred=unnecessary_assessment,
        life=unnecessary_assessment,
        kind_of=unnecessary_assessment,
    )

    assert selected is pool
    assert keys(selected) == [6, 5, 4, 3, 2, 1]


@pytest.mark.parametrize(
    "lively,expected",
    [
        ({1, 3, 5, 7, 9, 11}, [1, 2, 5, 7, 8, 11]),
        ({1, 3}, [1, 2, 3, 4, 8, 12]),
    ],
)
def test_without_relations_keeps_established_favourite_life_and_spread_order(lively, expected):
    assert keys(shortlist(choices(), stars={2, 8}, lively=lively)) == expected


@pytest.mark.parametrize("familiar_bundle", ["parent, child", " child , parent, child "])
def test_new_relations_preserve_the_span_without_reserving_familiar_combinations(
    familiar_bundle,
):
    pool = choices()
    original = [asdict(c) for c in pool]
    selected = shortlist(
        pool,
        stars={1, 4, 8},
        relations={
            1: "parent",
            2: "grandparent",
            3: familiar_bundle,
            4: "child",
            5: "sibling",
            8: "child, parent",
        },
    )

    assert keys(selected) == [1, 2, 4, 5, 8, 12]
    assert len(selected) == 6
    assert [asdict(c) for c in pool] == original
    assert all(any(chosen is source for source in pool) for chosen in selected)


def test_later_favourites_already_cover_their_relation_before_nonfavourites_are_considered():
    selected = shortlist(
        choices(),
        stars={1, 4, 6, 8, 10},
        relations={1: "parent", 2: "grandparent", 3: "sibling", 10: "grandparent"},
    )

    assert keys(selected) == [1, 3, 4, 6, 8, 10]


def test_new_relation_can_use_a_non_lively_nomination_before_remaining_lively_pictures():
    selected = shortlist(
        choices(),
        stars={1, 4, 6, 8, 10},
        lively={1, 4, 5, 6, 7, 8, 9, 10, 11, 12},
        relations={1: "parent", 2: "grandparent"},
    )

    assert keys(selected) == [1, 2, 4, 6, 8, 10]


def test_life_on_an_alternative_retains_the_existing_lively_priority():
    pool = choices()
    pool[1].alternatives = ["asset-99"]
    assert keys(shortlist(pool, lively={99})) == [1, 2, 4, 7, 10, 12]


@pytest.mark.parametrize("grant,limit", [(1, 6), (2, 6), (3, 9)])
def test_favourites_that_fill_capacity_keep_their_order_and_cannot_be_displaced(grant, limit):
    pool = choices(15)

    def unnecessary_relations(_choice):
        raise AssertionError("Relationship preference cannot displace a favourite")

    selected = shortlist_story_moments(
        pool,
        grant,
        starred=lambda c: 2 <= int(c.key) <= 13,
        life=lambda _asset: True,
        kind_of=unnecessary_relations,
    )

    assert keys(selected) == list(range(2, 2 + limit))
    assert len(selected) == limit


def test_earliest_relation_nomination_does_not_guarantee_later_audience_admission():
    # An early tagged picture may later be held. This shortlist never clears that hold or
    # promises that another picture carrying the same relation will survive the same cap.
    selected = shortlist(
        choices(),
        stars={1, 4, 6, 8, 10},
        relations={1: "parent", 2: "grandparent", 3: "grandparent"},
    )

    assert keys(selected) == [1, 2, 4, 6, 8, 10]
    admitted = [c for c in selected if c.key != "2"]
    assert keys(admitted) == [1, 4, 6, 8, 10]


def test_one_new_relation_does_not_resample_unrelated_stages():
    pool = choices(28)
    baseline = shortlist(pool, grant=3, stars={6, 14, 23})
    selected = shortlist(
        pool,
        grant=3,
        stars={6, 14, 23},
        relations={6: "child", 3: "grandparent"},
    )

    before, after = set(keys(baseline)), set(keys(selected))
    assert after - before == {3}
    assert len(before - after) == 1
    assert {6, 14, 23} <= after
    assert keys(selected)[0] == keys(baseline)[0]
    assert keys(selected)[-1] == keys(baseline)[-1]


def test_an_already_sampled_nominee_does_not_move_other_stages():
    pool = choices(28)
    baseline = shortlist(pool, grant=3, stars={6, 14, 23})
    nominee = next(k for k in keys(baseline)[1:] if k not in {6, 14, 23})

    assert (
        shortlist(
            pool,
            grant=3,
            stars={6, 14, 23},
            relations={nominee: "grandparent"},
        )
        == baseline
    )


def test_local_replacement_cannot_lose_another_sampled_relationship():
    pool = choices()
    # The baseline samples 1, 3, 5, 8, 10, 12. The closest interior slot (3)
    # is the only sibling view; inserting a grandparent must retain it.
    selected = shortlist(pool, relations={2: "grandparent", 3: "sibling"})

    assert {1, 2, 3, 12} <= set(keys(selected))
    assert len(selected) == 6


def test_a_moment_that_plays_is_taken_before_an_equivalent_still():
    """Twelve moments, a grant of one: six fit. The three videos are all kept, the stars first."""
    moving = {4, 9, 11}
    selected = shortlist_story_moments(
        choices(12),
        1,
        starred=lambda c: int(c.key) == 2,
        life=lambda _asset: True,
        plays=lambda c: int(c.key) in moving,
    )

    assert set(keys(selected)) >= moving | {2}
    assert len(selected) == 6
    assert keys(selected) == sorted(keys(selected))  # the shortlist stays chronological


def test_without_moving_moments_the_sample_is_unchanged():
    pool = choices(12)
    assert keys(shortlist(pool)) == keys(
        shortlist_story_moments(
            pool, 1, starred=lambda _c: False, life=lambda _a: True, plays=lambda _c: False
        )
    )


def test_a_video_behind_a_still_keeps_its_capture_group_in_the_sample():
    """The inventory can only find the video moment inside a group the shortlist kept."""
    from immich_memories.analysis.editorial_story_carriers import shortlist_by_partition
    from immich_memories.analysis.editorial_story_slots import PartitionedSlots

    pool = choices(12)
    pool[1].alternatives = ["clip-2"]  # the second group's still leads, its video follows
    units = {
        c.primary: ("", {"kind": "still", "taken": c.taken, "moment": c.key}) for c in pool
    } | {"clip-2": ("", {"kind": "video", "taken": pool[1].taken, "moment": "2"})}

    selected = shortlist_by_partition(
        pool,
        {None: 1},
        PartitionedSlots(units),
        units,
        starred=lambda _c: False,
        life=lambda _asset: False,
        kind_of=lambda _c: "",
    )

    assert 2 in keys(selected)
