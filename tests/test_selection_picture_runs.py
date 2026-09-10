"""Chronological neighbours chain into runs, and this library calibrates its own distance."""

from __future__ import annotations

from immich_memories.analysis.selection_same_picture import (
    SELECTS_CALIBRATION_PAIRS,
    SELECTS_MAX_CORROBORATION,
    Corroboration,
    parallel_pair_decisions,
    runs_from_pair_decisions,
    runs_of_one_picture,
)
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from tests.test_selection_same_picture import Atlas, Requester, candidate, frames, neighbours


def chronological(tmp_path, members, *, answers=None, shades=None, corroboration=None):
    ids = [c.asset_id for c in members]
    atlas = Atlas(shades or dict.fromkeys(ids, 200))
    requester = Requester(answers)
    decisions, warnings = parallel_pair_decisions(
        [("moment", members)],
        atlas=atlas,
        requester=requester,
        sheet_output_dir=tmp_path / "sheets",
        limits=VisionRequestLimits(),
        corroboration=corroboration or Corroboration(),
        concurrency=2,
    )
    return decisions, warnings, requester


def test_agreeing_neighbours_chain_into_one_run_and_a_disagreement_starts_another(tmp_path):
    members = frames(4)

    decisions, warnings, _ = chronological(
        tmp_path,
        members,
        answers={neighbours(1): (False, True)},
        shades={f"frame-{n}": 40 * n for n in range(4)},
    )
    runs, run_warnings = runs_from_pair_decisions("moment", members, decisions)

    assert [[c.asset_id for c in run] for run in runs] == [
        ["frame-0", "frame-1"],
        ["frame-2", "frame-3"],
    ]
    assert run_warnings == () and warnings == ()


def test_an_unreadable_neighbour_answer_keeps_both_frames_and_says_so(tmp_path):
    members = frames(2)

    decisions, _, _ = chronological(tmp_path, members, answers={neighbours(0): ("nonsense", True)})
    runs, warnings = runs_from_pair_decisions("moment", members, decisions)

    assert [[c.asset_id for c in run] for run in runs] == [["frame-0"], ["frame-1"]]
    assert warnings == ("!! Pass 2 unreadable pair answer, both kept: moment-0",)


def test_a_moment_of_one_picture_asks_nothing(tmp_path):
    decisions, warnings, requester = chronological(tmp_path, [candidate("only")])

    assert decisions == {} and warnings == ()
    assert requester.arrangements == []


def test_a_settled_library_stops_buying_the_reverse_arrangement_below_its_own_distance(tmp_path):
    members = frames(2)

    decisions, _, requester = chronological(
        tmp_path, members, corroboration=Corroboration(distance=4, calibrating=False)
    )

    assert decisions["moment-0"] == (True, None)
    assert requester.arrangements == ["selects/pair/ab"]


def test_calibration_settles_on_this_librarys_own_pairs_and_reports_the_distance(tmp_path):
    corroboration = Corroboration()

    _, warnings, _ = chronological(
        tmp_path, frames(SELECTS_CALIBRATION_PAIRS + 2), corroboration=corroboration
    )

    assert corroboration.calibrating is False
    assert corroboration.distance == SELECTS_MAX_CORROBORATION
    assert warnings and "calibrated on" in warnings[0]


def test_a_contradicting_library_may_only_lower_the_measured_cap():
    corroboration = Corroboration(
        observations=[(1, True, False), *[(20, True, True)] * SELECTS_CALIBRATION_PAIRS]
    )

    assert corroboration.settle() is not None
    assert corroboration.distance == 0


def test_the_sequential_route_reaches_the_same_runs_as_the_concurrent_one(tmp_path):
    members = frames(3)

    runs, warnings = runs_of_one_picture(
        "moment",
        members,
        atlas=Atlas({f"frame-{n}": 40 * n for n in range(3)}),
        requester=Requester({neighbours(1): (False, True)}),
        sheet_output_dir=tmp_path / "sheets",
        limits=VisionRequestLimits(),
        corroboration=Corroboration(),
    )

    assert [[c.asset_id for c in run] for run in runs] == [["frame-0", "frame-1"], ["frame-2"]]
    assert warnings == ()


def test_the_sequential_route_also_keeps_both_frames_on_an_unreadable_answer(tmp_path):
    members = frames(2)

    runs, warnings = runs_of_one_picture(
        "moment",
        members,
        atlas=Atlas({"frame-0": 10, "frame-1": 250}),
        requester=Requester({neighbours(0): (True, "nonsense")}),
        sheet_output_dir=tmp_path / "sheets",
        limits=VisionRequestLimits(),
        corroboration=Corroboration(distance=0, calibrating=False),
    )

    assert [[c.asset_id for c in run] for run in runs] == [["frame-0"], ["frame-1"]]
    assert warnings == ("!! Pass 2 unreadable pair answer, both kept: moment-0",)
