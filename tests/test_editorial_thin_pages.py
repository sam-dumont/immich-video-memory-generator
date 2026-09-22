"""What a slot is offered, and in which order."""

from __future__ import annotations

from immich_memories.analysis.editorial_thin_catalogue import BankedCatalogue, ThinStory
from immich_memories.analysis.editorial_thin_pages import (
    gate_refill_page,
    motion_first,
    newcomer_stories,
    records_first,
)


def unit(asset, moment, *, kind="still", favourite=False):
    return {"asset_id": asset, "moment": moment, "kind": kind, "favourite": favourite}


def test_a_moment_that_moves_leads_the_page_and_the_others_keep_their_order():
    page = motion_first(
        [
            unit("s1", "m1"),
            unit("s2", "m2"),
            unit("v3", "m3", kind="video"),
            unit("s4", "m4"),
            unit("lp5", "m5", kind="live-motion"),
        ]
    )
    assert [row["asset_id"] for row in page] == ["v3", "lp5", "s1", "s2", "s4"]


def test_inside_one_moment_the_owners_star_wins_its_own_frame():
    """A video that carries the moment beats a still, but not the frame the owner starred."""
    page = motion_first(
        [
            unit("clip", "m1", kind="video"),
            unit("starred", "m1", favourite=True),
            unit("plain", "m2"),
        ]
    )
    assert [row["asset_id"] for row in page] == ["starred", "clip", "plain"]


def test_a_page_with_no_motion_at_all_keeps_the_order_its_moments_arrived_in():
    rows = [unit("a", "m1"), unit("b", "m2")]
    assert [row["asset_id"] for row in motion_first(rows)] == ["a", "b"]
    # a moment is offered whole, because deciding a star against a clip means comparing the
    # pictures of one moment with each other
    split = [unit("a", "m1"), unit("b", "m2"), unit("c", "m1")]
    assert [row["asset_id"] for row in motion_first(split)] == ["a", "c", "b"]


def test_a_record_outranks_a_clip_and_motion_only_breaks_the_tie():
    records = {"still-rec": "a sign", "clip-rec": "a sign"}
    page = records_first(
        [
            unit("still-plain", "m1"),
            unit("clip-plain", "m2", kind="video"),
            unit("still-rec", "m3"),
            unit("clip-rec", "m4", kind="video"),
        ],
        records.get,
    )
    assert [row["asset_id"] for row in page] == [
        "clip-rec",
        "still-rec",
        "clip-plain",
        "still-plain",
    ]


def test_a_gate_emptied_slot_is_offered_its_own_moment_then_the_unused_ones():
    rows = [
        unit("r1", "m2"),
        unit("r2", "m1"),
        unit("r3", "m3"),
        unit("r4", "m1"),
        unit("r5", "m4"),
    ]
    assert [row["asset_id"] for row in gate_refill_page(rows, ["m1"], {"m3"})] == [
        "r2",
        "r4",
        "r1",
        "r5",
        "r3",
    ]
    # two refused shots of one story: their moments lead in the order they were refused
    assert [row["asset_id"] for row in gate_refill_page(rows, ["m4", "m1"], {"m3"})] == [
        "r5",
        "r2",
        "r4",
        "r1",
        "r3",
    ]
    # nothing to say about moments: the moments keep the order they arrived in, each whole
    assert [row["asset_id"] for row in gate_refill_page(rows, [], set())] == [
        "r1",
        "r2",
        "r4",
        "r3",
        "r5",
    ]


def story(key, tier, day, assets):
    return ThinStory(
        key=key,
        title=key,
        purpose="",
        episodes=(key,),
        tier=tier,
        first_day=day,
        asset_ids=tuple(assets),
    )


def test_a_record_owning_story_with_no_voice_is_offered_a_seat_in_the_stated_order():
    catalogue = BankedCatalogue(
        thesis="an account",
        stories=(
            story("S1", "remarkable", "2024-02-01", ["a1"]),
            story("S2", "maybe", "2024-02-09", ["a2"]),
            story("S3", "maybe", "2024-02-03", ["a3", "a4"]),
            story("S4", "maybe", "2024-02-12", ["a5"]),
            story("S5", "maybe", "2024-02-13", ["a6"]),
        ),
        hints={},
        records={"a1": "r", "a2": "r", "a3": "r", "a4": "r", "a5": "r"},
    )
    rows = newcomer_stories(
        catalogue,
        held={"S1"},
        covered_days={"2024-02-03"},
        offers=lambda key: key != "S5",
    )
    # S1 already speaks, S5 holds no candidate. Of the rest the tiers tie, so an unreached day
    # leads, then the story holding more records.
    assert [row.key for row in rows] == ["S2", "S4", "S3"]


def test_a_story_the_catalogue_records_nothing_about_is_never_a_newcomer():
    catalogue = BankedCatalogue(
        thesis="an account",
        stories=(story("S1", "remarkable", "2024-02-01", ["a1"]),),
        hints={},
        records={},
    )
    assert newcomer_stories(catalogue, held=set(), covered_days=set(), offers=lambda _k: True) == []
