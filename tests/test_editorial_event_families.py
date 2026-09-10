"""Episodes join one happening only when the day, the place and the gap all agree."""

from __future__ import annotations

import pytest

from immich_memories.analysis.editorial_event_families import (
    MERGE_GAP_SECONDS,
    merge_event_families,
)

FIELDS = ("id", "taken", "span_s", "location_head")


def tables(moments, places=()):
    return {
        "moments": (FIELDS, [(m["id"], m["taken"], m["span_s"], m["head"]) for m in moments]),
        "moment_places": (("moment", "place"), list(places)),
    }


def families(moments, *, mapping=None, places=(), assets_of=None, gps=None, order=None):
    ids = [moment["id"] for moment in moments]
    return merge_event_families(
        tables(moments, places),
        order or ids,
        mapping or {moment_id: f"episode-{moment_id}" for moment_id in ids},
        assets_of=assets_of or {},
        gps=gps or {},
    )


def moment(moment_id, hour, *, head="seaside", span=60, day="2021-06-05"):
    return {"id": moment_id, "taken": f"{day}T{hour:02d}:00:00", "span_s": span, "head": head}


def test_two_episodes_of_one_afternoon_in_one_place_become_one_happening():
    family_of, log = families([moment("a", 14), moment("b", 15)])

    assert family_of == {"a": "episode-a", "b": "episode-a"}
    assert log["episodes_before"] == 2
    assert log["families_after"] == 1
    assert log["merged_families"] == {"episode-a": ["episode-a", "episode-b"]}


def test_a_long_enough_gap_makes_the_evening_its_own_happening():
    gap_hours = MERGE_GAP_SECONDS // 3600 + 2

    family_of, log = families([moment("a", 9), moment("b", 9 + gap_hours)])

    assert family_of == {"a": "episode-a", "b": "episode-b"}
    assert log["families_after"] == 2
    assert log["merged_families"] == {}


def test_a_different_day_is_never_the_same_happening():
    family_of, _ = families([moment("a", 22), moment("b", 0, day="2021-06-06")])

    assert family_of == {"a": "episode-a", "b": "episode-b"}


def test_a_different_place_name_is_never_the_same_happening():
    family_of, _ = families([moment("a", 14), moment("b", 15, head="the woods")])

    assert family_of == {"a": "episode-a", "b": "episode-b"}


def test_episodes_far_apart_on_the_map_stay_apart_however_close_in_time():
    near, _ = families(
        [moment("a", 14), moment("b", 15)],
        assets_of={"a": ["one"], "b": ["two"]},
        gps={"one": (50.85, 4.35), "two": (50.86, 4.36)},
    )
    far, _ = families(
        [moment("a", 14), moment("b", 15)],
        assets_of={"a": ["one"], "b": ["two"]},
        gps={"one": (50.85, 4.35), "two": (48.85, 2.35)},
    )

    assert near == {"a": "episode-a", "b": "episode-a"}
    assert far == {"a": "episode-a", "b": "episode-b"}


def test_without_coordinates_a_shared_place_holds_the_happening_together():
    shared, _ = families(
        [moment("a", 14), moment("b", 15)],
        places=[("a", "park-7"), ("b", "park-7")],
    )
    disjoint, _ = families(
        [moment("a", 14), moment("b", 15)],
        places=[("a", "park-7"), ("b", "hall-2")],
    )

    assert shared == {"a": "episode-a", "b": "episode-a"}
    assert disjoint == {"a": "episode-a", "b": "episode-b"}


def test_an_episode_with_no_place_at_all_is_not_kept_out_by_one():
    family_of, _ = families([moment("a", 14), moment("b", 15)], places=[("a", "park-7")])

    assert family_of == {"a": "episode-a", "b": "episode-a"}


def test_two_threads_in_two_places_remain_two_happenings_however_they_interleave():
    moments = [
        moment("a", 12),
        moment("b", 13, head="the woods"),
        moment("c", 14),
    ]

    family_of, log = families(moments)

    assert family_of == {"a": "episode-a", "b": "episode-b", "c": "episode-c"}
    assert log["interleave_merges"] == 0
    assert log["rule"]["interleaved_episodes_unioned"] is False


def test_every_moment_of_one_episode_carries_that_episodes_family():
    moments = [moment("a", 14), moment("b", 14, span=600), moment("c", 20)]
    mapping = {"a": "morning", "b": "morning", "c": "evening"}

    family_of, log = families(moments, mapping=mapping)

    assert family_of == {"a": "morning", "b": "morning", "c": "evening"}
    assert log["episodes_before"] == 2


@pytest.mark.parametrize("head", ["", "null"])
def test_an_absent_location_head_does_not_become_a_place_name(head):
    family_of, _ = families([moment("a", 14, head=head), moment("b", 15, head=head)])

    assert family_of == {"a": "episode-a", "b": "episode-a"}


def test_an_episode_takes_the_place_name_most_of_its_moments_agree_on():
    moments = [
        moment("a", 14, head="seaside"),
        moment("b", 14, head="seaside"),
        moment("c", 14, head="the pier"),
        moment("d", 15, head="seaside"),
    ]
    mapping = {"a": "first", "b": "first", "c": "first", "d": "second"}

    family_of, _ = families(moments, mapping=mapping)

    assert family_of == {"a": "first", "b": "first", "c": "first", "d": "first"}


def test_the_rule_the_grouping_ran_under_is_reported_with_its_own_numbers():
    _, log = families([moment("a", 14)])

    assert log["rule"]["max_gap_seconds"] == MERGE_GAP_SECONDS
    assert log["rule"]["near_km_by_gps"] == 10.0
    assert log["rule"]["same_day"] is True
    assert log["rule"]["fallback_without_gps"] == "shared place id or none"
