"""Resolved duration, required coverage and incremental motion bound reserve work."""

import pytest

from immich_memories.analysis.editorial_completion import RetainedMotion


def picture(key, seconds, *, moving=False):
    return {
        "asset_id": key,
        "members": [key],
        "raw_seconds": seconds,
        "seconds": seconds,
        "motion_candidate": moving,
        "why": "Visible activity",
    }


def test_provisional_motion_near_target_must_be_resolved_before_stopping():
    def resolve(rows):
        return [{**row, "seconds": 4, "kind": "live-still"} for row in rows], {}

    provisional = [picture(str(i), 6, moving=True) for i in range(25)]
    resolved = RetainedMotion(resolve)(provisional)
    assert sum(c["seconds"] for c in provisional) == 150
    assert sum(c["seconds"] for c in resolved) == 100


def test_only_new_retained_units_are_resolved_and_metrics_count_actual_work():
    calls = []

    def resolve(rows):
        calls.append([row["asset_id"] for row in rows])
        return (
            [
                {**row, "seconds": 4, "motion_assessed": False, "motion_evidence": {"available": 0}}
                for row in rows
            ],
            {
                "candidate_carriers": len(rows),
                "unavailable_sources": len(rows),
                "new_motion_downloads": 0,
                "wall_seconds": 0.125,
                "sample_limit_per_carrier": 3,
                "method": "test",
            },
        )

    motion = RetainedMotion(resolve)
    first = motion([picture("a", 6, moving=True)])
    first[0]["why"] = "Updated editorial explanation"
    second = motion([*first, picture("b", 6, moving=True)])
    assert motion(second) == second
    assert calls == [["a"], ["b"]]
    assert second[0]["why"] == "Updated editorial explanation"
    assert second[0]["seconds"] == second[1]["seconds"] == 4
    assert motion.metrics["candidate_carriers"] == 2
    assert motion.metrics["unavailable_sources"] == 2
    assert motion.metrics["wall_seconds"] == 0.25
    assert motion.metrics["sample_limit_per_carrier"] == 3


def test_changed_burst_membership_is_new_motion_evidence():
    calls = []

    def resolve(rows):
        calls.append(rows)
        return rows, {}

    motion = RetainedMotion(resolve)
    original = picture("a", 6, moving=True)
    motion([original])
    motion([{**original, "members": ["a", "b"]}])
    assert len(calls) == 2


def test_motion_cannot_replace_selected_pictures():
    motion = RetainedMotion(lambda rows: ([{**rows[0], "asset_id": "replacement"}], {}))
    with pytest.raises(ValueError, match="preserve retained unit"):
        motion([picture("a", 6, moving=True)])


def test_missing_motion_port_preserves_captured_duration_and_zero_work():
    motion = RetainedMotion(None)
    selected = [picture("a", 4)]
    assert motion(selected) == selected
    assert motion.metrics["new_motion_downloads"] == 0
