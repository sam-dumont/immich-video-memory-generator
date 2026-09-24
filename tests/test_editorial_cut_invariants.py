"""A finished cut is read against every promise its passes made, and names the pass that broke one."""

from __future__ import annotations

import json

from immich_memories.analysis.editorial_cut_invariants import (
    FinishedCut,
    broken_promises,
    cut_violations,
    report_violations,
)
from immich_memories.analysis.editorial_family_seat import FamilySeatPolicy


def _shot(asset_id: str, taken: str = "2024-02-01T10:00:00+00:00", **fields) -> dict:
    return {"asset_id": asset_id, "taken": taken, "kind": "still", "members": [asset_id]} | fields


def _shared(_asset: str) -> str:
    return "share"


def _broken(cut: FinishedCut) -> list[tuple[str, str, str]]:
    return [(v.invariant, v.subject, v.last_pass) for v in cut_violations(cut)]


def test_a_cut_that_keeps_every_promise_breaks_none():
    cut = FinishedCut(
        carriers=[_shot("a"), _shot("b", "2024-02-02T10:00:00+00:00")], verdict_of=_shared
    )

    assert cut_violations(cut) == []


def test_a_close_relative_left_without_a_shot_names_the_pass_that_took_it():
    mother = {"Person A": "mother"}
    cut = FinishedCut(
        carriers=[_shot("other")],
        verdict_of=_shared,
        scope=["seat", "p2", "p3", "other"],
        close_family_of=lambda a: mother if a != "other" else {},
        seat_policy=FamilySeatPolicy(min_pictures=3),
        removed_by={"seat": "final-duplicates"},
    )

    assert _broken(cut) == [("family_seat", "mother", "final-duplicates")]


def test_a_relative_the_seat_recorded_it_could_not_place_breaks_nothing():
    cut = FinishedCut(
        carriers=[_shot("other")],
        verdict_of=_shared,
        scope=["p1", "p2", "p3", "other"],
        close_family_of=lambda a: {"Person A": "father"} if a != "other" else {},
        seat_policy=FamilySeatPolicy(min_pictures=3),
        seat_gave_up=frozenset({"father"}),
    )

    assert cut_violations(cut) == []


def test_a_non_favourite_carrying_a_moment_whose_favourite_could_have_breaks_the_rule():
    units = {
        "star": {"asset_id": "star", "moment": "M1", "favourite": True, "members": ["star"]},
        "plain": {"asset_id": "plain", "moment": "M1", "favourite": False, "members": ["plain"]},
    }
    cut = FinishedCut(
        carriers=[_shot("plain", moment="M1", favourite=False)],
        units=units,
        drafted=frozenset({"star"}),
        removed_by={"star": "final-duplicates"},
        added_by={"plain": "final-duplicates"},
        verdict_of=_shared,
    )

    assert _broken(cut) == [("favourite_wins_its_moment", "plain", "final-duplicates")]


def test_a_favourite_the_film_refuses_leaves_its_moment_to_another_picture():
    units = {
        "star": {"asset_id": "star", "moment": "M1", "favourite": True, "members": ["star"]},
        "plain": {"asset_id": "plain", "moment": "M1", "favourite": False, "members": ["plain"]},
    }
    cut = FinishedCut(
        carriers=[_shot("plain", moment="M1", favourite=False)],
        units=units,
        verdict_of=_shared,
        showable=lambda a: a != "star",
    )

    assert cut_violations(cut) == []


def test_a_year_of_a_person_film_left_without_a_shot_is_named():
    cut = FinishedCut(
        carriers=[_shot("late", "2024-05-01T10:00:00+00:00")],
        verdict_of=_shared,
        era_of=lambda taken: f"year-{taken[:4]}",
        era_pictures={"year-2024": ["late"], "year-2012": ["quiet"]},
        drafted=frozenset({"late", "quiet"}),
        removed_by={"quiet": "timing-trim"},
    )

    assert _broken(cut) == [("every_year_has_a_voice", "year-2012", "timing-trim")]


def test_a_year_whose_pictures_failed_the_standing_bar_was_recorded():
    cut = FinishedCut(
        carriers=[_shot("late", "2024-05-01T10:00:00+00:00")],
        verdict_of=_shared,
        era_of=lambda taken: f"year-{taken[:4]}",
        era_pictures={"year-2024": ["late"], "year-2012": ["quiet"]},
        standing_refused=frozenset({"quiet"}),
    )

    assert cut_violations(cut) == []


def test_a_live_photo_that_moves_with_its_subject_in_frame_must_play():
    cut = FinishedCut(
        carriers=[_shot("live", kind="live-still", residual=2.4, video_ids=["clip"])],
        verdict_of=_shared,
    )

    assert _broken(cut) == [("live_motion_plays", "live", "motion resolution")]


def test_a_live_photo_whose_clip_misses_its_subject_stays_a_still():
    cut = FinishedCut(
        carriers=[_shot("live", kind="still", residual=None)],
        verdict_of=_shared,
        live_clip_of={"live": "clip"},
        residuals={"live": 3.0},
        clip_misses_subject=lambda clip: clip == "clip",
    )

    assert cut_violations(cut) == []


def test_a_film_whose_policy_turns_live_motion_off_promises_none():
    cut = FinishedCut(
        carriers=[_shot("live", kind="live-still", residual=2.4, video_ids=["clip"])],
        verdict_of=_shared,
        live_motion=False,
    )

    assert cut_violations(cut) == []


def test_a_picture_the_gate_holds_or_a_carrier_rule_refuses_cannot_ship():
    cut = FinishedCut(
        carriers=[
            _shot("held"),
            _shot("receipt", "2024-02-02T10:00:00+00:00"),
            _shot("unseen", "2024-02-03T10:00:00+00:00"),
        ],
        audience="shared",
        verdict_of={"held": "family_only", "receipt": "share"}.get,
        refused_by_rule={"receipt": "document-head:receipt"},
        drafted=frozenset({"held", "receipt"}),
        added_by={"unseen": "final-duplicates"},
    )

    assert _broken(cut) == [
        ("nothing_refused_ships", "held", "draft"),
        ("nothing_refused_ships", "receipt", "draft"),
        ("nothing_refused_ships", "unseen", "final-duplicates"),
    ]


def test_a_shot_placed_before_an_earlier_one_breaks_the_chronology():
    cut = FinishedCut(
        carriers=[_shot("later", "2024-02-05T10:00:00+00:00"), _shot("seat", family_seat=True)],
        verdict_of=_shared,
        added_by={"seat": "family-seat"},
    )

    assert _broken(cut) == [("chronological", "seat", "family-seat")]


def test_a_run_records_what_it_broke_and_runs_show_counts_it(tmp_path, caplog):
    decisions = tmp_path / "derived-decisions"
    decisions.mkdir()
    violations = cut_violations(
        FinishedCut(carriers=[_shot("b", "2024-02-05T10:00:00+00:00"), _shot("a")])
    )

    def record(name, payload):
        (decisions / f"{name}.private.json").write_text(json.dumps(payload))

    with caplog.at_level("WARNING"):
        report_violations(violations, record)

    assert broken_promises(tmp_path) == len(violations) == 3
    assert "Cut invariant chronological broken: a" in caplog.text
    assert broken_promises(tmp_path / "elsewhere") is None


def test_runs_show_says_how_many_promises_the_cut_broke(tmp_path):
    from immich_memories.cli._helpers import console
    from immich_memories.cli.runs import _print_cut_checks
    from immich_memories.operations.run_index import record_run_attempt

    attempt = tmp_path / "editorial-runs" / "film" / "attempts" / "one"
    (attempt / "derived-decisions").mkdir(parents=True)
    report_violations(
        cut_violations(FinishedCut(carriers=[_shot("a")])),
        lambda name, payload: (attempt / "derived-decisions" / f"{name}.private.json").write_text(
            json.dumps(payload)
        ),
    )
    record_run_attempt(tmp_path, "20260924_100000_abcd", attempt, tmp_path / "film.mp4")

    with console.capture() as captured:
        _print_cut_checks(tmp_path, "20260924_100000_abcd")

    assert "Cut checks: 1 broken promise(s)" in captured.get()
