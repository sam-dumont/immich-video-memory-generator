"""Which seats a polish opens, and the transaction that fills one."""

from __future__ import annotations

from immich_memories.analysis.editorial_thin_catalogue import BankedCatalogue, ThinStory
from immich_memories.analysis.editorial_thin_gates import GateRefusal
from immich_memories.analysis.editorial_thin_refill import openable_slots, plan_slots, seat


def shot(asset, story, *, day="01", seconds=4.0, moment=None, kind="still"):
    return {
        "asset_id": asset,
        "story_episode": story,
        "taken": f"2024-02-{day}T09:00:00",
        "moment": moment or f"m-{asset}",
        "seconds": seconds,
        "kind": kind,
    }


def story(key, assets, *, tier="maybe", day="2024-02-01"):
    return ThinStory(
        key=key,
        title=key,
        purpose="",
        episodes=(key,),
        tier=tier,
        first_day=day,
        asset_ids=tuple(assets),
    )


def catalogue(stories, records=None):
    return BankedCatalogue(
        thesis="an account", stories=tuple(stories), hints={}, records=records or {}
    )


def verdict(state, named=0):
    return {"state": state, "named_by": named, "why": "", "protected": False, "held_by": ""}


def test_no_seat_is_opened_into_a_film_with_no_room_for_a_whole_carrier():
    assert openable_slots(60.0, 24.0) == 10
    assert openable_slots(60.0, 57.0) == 0
    assert openable_slots(60.0, 61.0) == 0


def test_a_shot_both_orders_named_is_replaced_from_its_own_story_only():
    cut = [shot("a1", "S1"), shot("a2", "S2", day="02")]
    slots = plan_slots(
        cut,
        catalogue=catalogue([story("S1", ["a1", "spare"]), story("S2", ["a2"])]),
        verdicts={"a1": verdict("bad", 2), "a2": verdict("kept")},
        refused=[],
        candidates_of={"S1": [shot("spare", "S1")]}.get,
        seen={"a1", "a2"},
        content_cap=60.0,
    )
    assert [(s.key, s.story, s.kind) for s in slots] == [("R001", "S1", "vote-bad")]
    assert [row["asset_id"] for row in slots[0].page] == ["spare"]


def test_a_shot_the_gates_refused_is_offered_its_own_moment_first():
    cut = [shot("a1", "S1")]
    refused = [GateRefusal("g2", "S2", "standing", "weak", moment="q1")]
    pool = {"S2": [shot("other", "S2", moment="q9"), shot("twin", "S2", moment="q1")]}
    slots = plan_slots(
        cut,
        catalogue=catalogue([story("S1", ["a1"]), story("S2", ["g2", "other", "twin"])]),
        verdicts={"a1": verdict("kept")},
        refused=refused,
        candidates_of=pool.get,
        seen={"a1", "g2"},
        content_cap=60.0,
    )
    assert [(s.key, s.story, s.kind) for s in slots] == [("T001", "S2", "gate-refused")]
    assert [row["asset_id"] for row in slots[0].page] == ["twin", "other"]


def test_a_shot_one_order_doubted_keeps_its_place_under_a_swap():
    cut = [shot("a1", "S1")]
    slots = plan_slots(
        cut,
        catalogue=catalogue([story("S1", ["a1", "spare"])]),
        verdicts={"a1": verdict("weak", 1)},
        refused=[],
        candidates_of={"S1": [shot("spare", "S1")]}.get,
        seen={"a1"},
        content_cap=60.0,
    )
    assert [(s.key, s.kind, s.replacing) for s in slots] == [("D901", "vote-weak", "a1")]


def test_a_swap_is_opened_even_when_the_film_has_no_room_left():
    """It frees its own place; an appended seat does not."""
    cut = [shot("a1", "S1", seconds=59.0)]
    slots = plan_slots(
        cut,
        catalogue=catalogue([story("S1", ["a1", "spare"])]),
        verdicts={"a1": verdict("weak", 1)},
        refused=[],
        candidates_of={"S1": [shot("spare", "S1")]}.get,
        seen={"a1"},
        content_cap=60.0,
    )
    assert [s.kind for s in slots] == ["vote-weak"]


def test_a_story_the_gates_emptied_is_not_also_given_a_newcomer_seat():
    cut = [shot("a1", "S1")]
    refused = [GateRefusal("g2", "S2", "standing", "weak", moment="q1")]
    pool = {"S2": [shot("twin", "S2", moment="q1")], "S3": [shot("new", "S3")]}
    slots = plan_slots(
        cut,
        catalogue=catalogue(
            [story("S1", ["a1"]), story("S2", ["g2", "twin"]), story("S3", ["new"])],
            records={"twin": "a record", "new": "a record"},
        ),
        verdicts={"a1": verdict("kept")},
        refused=refused,
        candidates_of=pool.get,
        seen={"a1", "g2"},
        content_cap=60.0,
    )
    # S2 owns a record and has no voice, but its seat is the gate refill, not a second one
    assert sorted((s.key, s.story) for s in slots) == [("N001", "S3"), ("T001", "S2")]


def test_a_gate_refused_shot_is_never_offered_back_into_the_film():
    """A seat opened because a shot was refused must not be handed that same shot first: it
    fails the same gate again and burns the attempt."""
    refused = [GateRefusal("g2", "S2", "standing", "weak", moment="q1")]
    pool = {"S2": [shot("g2", "S2", moment="q1"), shot("alive", "S2", moment="q1")]}
    slots = plan_slots(
        [shot("a1", "S1")],
        catalogue=catalogue([story("S1", ["a1"]), story("S2", ["g2", "alive"])]),
        verdicts={"a1": verdict("kept")},
        refused=refused,
        candidates_of=pool.get,
        seen={"a1", "g2"},
        content_cap=60.0,
    )
    assert [row["asset_id"] for row in slots[0].page] == ["alive"]


def test_seating_a_replacement_leaves_the_cut_untouched_until_it_passes():
    cut = [shot("a1", "S1", seconds=4.0), shot("a2", "S2", day="02", seconds=4.0)]
    after, changed = seat(cut, shot("spare", "S1", day="03"), replacing="a1", content_cap=60.0)
    assert changed
    assert [row["asset_id"] for row in after] == ["a2", "spare"]
    assert [row["asset_id"] for row in cut] == ["a1", "a2"]


def test_a_replacement_never_runs_longer_than_the_shot_it_replaces():
    cut = [shot("a1", "S1", seconds=3.0)]
    after, _changed = seat(cut, shot("spare", "S1", seconds=6.0), replacing="a1", content_cap=60.0)
    assert after[0]["seconds"] == 3.0


def test_a_newcomer_is_trimmed_by_its_overrun_and_refused_below_a_clips_floor():
    cut = [shot("a1", "S1", seconds=55.0)]
    after, changed = seat(
        cut, shot("new", "S2", day="02", seconds=6.0), replacing="", content_cap=58.0
    )
    assert changed and after[1]["seconds"] == 3.0
    _same, changed = seat(
        cut, shot("new", "S2", day="02", seconds=3.0), replacing="", content_cap=56.5
    )
    assert not changed


def test_a_shot_the_cut_already_holds_is_never_seated_twice():
    cut = [shot("a1", "S1")]
    after, changed = seat(cut, shot("a1", "S1"), replacing="", content_cap=60.0)
    assert not changed and [row["asset_id"] for row in after] == ["a1"]


def test_a_refill_page_inside_a_drafted_story_leads_with_what_the_catalogue_records():
    """A record is the catalogue saying a picture matters; a seat in a story the draft already
    speaks for is offered it first, exactly as a newcomer seat is."""
    cut = [shot("a1", "S1"), shot("b1", "S2", day="02"), shot("c1", "S3", day="03")]
    refused = [GateRefusal("g2", "S2", "standing", "weak", moment="q1")]
    pool = {
        "S1": [shot("plain", "S1", moment="p1"), shot("rec1", "S1", moment="p2")],
        "S2": [shot("twin", "S2", moment="q1"), shot("rec2", "S2", moment="q7")],
        "S3": [shot("other", "S3", moment="r1"), shot("rec3", "S3", moment="r2")],
    }
    slots = plan_slots(
        cut,
        catalogue=catalogue(
            [
                story("S1", ["a1", "plain", "rec1"]),
                story("S2", ["b1", "g2", "twin", "rec2"]),
                story("S3", ["c1", "other", "rec3"]),
            ],
            records={"rec1": "a first", "rec2": "a first", "rec3": "a first"},
        ),
        verdicts={"a1": verdict("bad", 2), "b1": verdict("kept"), "c1": verdict("weak", 1)},
        refused=refused,
        candidates_of=pool.get,
        seen={"a1", "b1", "c1", "g2"},
        content_cap=60.0,
    )
    pages = {slot.kind: [row["asset_id"] for row in slot.page] for slot in slots}
    assert pages["vote-bad"][0] == "rec1"
    assert pages["gate-refused"][0] == "rec2"
    assert pages["vote-weak"][0] == "rec3"


def test_a_removal_that_held_less_than_a_clips_floor_is_still_refilled_at_the_floor():
    """The 2024 year (09-25): the vote removed a 1.85 s live photo from a film at its length,
    and no refill could fit in 1.85 s. The refill takes the floor; finishing shaves the rest."""
    cut = [shot("a1", "S1", seconds=58.0)]
    after, changed = seat(
        cut, shot("new", "S1", day="02", seconds=4.0), replacing="", content_cap=58.0, frees=1.85
    )
    assert changed and after[1]["seconds"] == 2.0
