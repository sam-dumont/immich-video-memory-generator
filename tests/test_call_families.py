"""Stage labels collapse into the families a performance measurement is read by."""

import pytest

from immich_memories.operations.call_families import calls_by_family, family_of


@pytest.mark.parametrize(
    "stage,family",
    [
        ("worthy-1-source", "worthy"),
        ("worthy-1-hashed", "worthy"),
        ("story-episodes-2", "story-episodes"),
        ("story-episodes-2-repair", "story-episodes"),
        ("story-understanding-1", "story-understanding"),
        ("story-understanding-1-try2", "story-understanding"),
        ("story-weighing-source", "story-weighing"),
        ("story-weighing-reversed", "story-weighing"),
        ("story-weighing-hashed", "story-weighing"),
        ("moment-inventory-S0001-3", "moment-inventory"),
        ("standing-3-source", "standing"),
        ("standing-3-hashed", "standing"),
        ("story-pick-K01-source", "story-pick"),
        ("story-pick-K01-reversed", "story-pick"),
        ("story-pick-K01-repair", "story-pick"),
        ("shareability-07-activity", "shareability"),
        ("shareability-07-exposure-1", "shareability"),
        ("period", "period"),
        ("episodes", "episodes"),
        ("episode-skim-1", "episode-skim"),
    ],
)
def test_family_of_keeps_the_stage_name_and_drops_the_run_and_the_retry_words(stage, family):
    assert family_of(stage) == family


def test_calls_by_family_totals_every_call_and_counts_the_reused_answers():
    calls = [
        {"stage": "worthy-1-source", "cache_hit": True, "wall_seconds": 0.5},
        {"stage": "worthy-1-hashed", "cache_hit": False, "wall_seconds": 1.25},
        {"stage": "story-pick-K01-source", "cache_hit": False, "wall_seconds": 2.0},
        {"stage": "story-pick-K01-repair", "cache_hit": True, "wall_seconds": 0.0},
        {"stage": "period", "cache_hit": False, "wall_seconds": 3.5},
    ]

    families = calls_by_family(calls)

    assert sum(row["asked"] for row in families.values()) == len(calls)
    assert sum(row["cache_hits"] for row in families.values()) == 2
    assert families["worthy"] == {"asked": 2, "cache_hits": 1, "wall_seconds": 1.75}
    assert families["story-pick"] == {"asked": 2, "cache_hits": 1, "wall_seconds": 2.0}
    assert families["period"] == {"asked": 1, "cache_hits": 0, "wall_seconds": 3.5}


def test_calls_by_family_tolerates_a_call_row_without_timing():
    assert calls_by_family([{"stage": "period", "cache_hit": False}]) == {
        "period": {"asked": 1, "cache_hits": 0, "wall_seconds": 0.0}
    }
