"""A replacement's context names its own moment, never the refused carrier's."""

from __future__ import annotations

from immich_memories.analysis.editorial_story_planner import (
    StorySelection,
    alternatives_pool,
)
from immich_memories.analysis.editorial_story_reading import PeriodStory, StoryEpisode


def selection(carriers, alternatives_of):
    story = PeriodStory(
        thesis="",
        episodes=[
            StoryEpisode(
                key="D1",
                title="the canal morning",
                account="",
                significance="",
                role="supporting",
                uncertainty="",
                moments=["M1", "M2"],
            ),
            StoryEpisode(
                key="D2",
                title="the trip",
                account="",
                significance="",
                role="minor",
                uncertainty="",
                moments=["M3"],
            ),
        ],
        connections=[],
        priorities=[],
        uncertainties=[],
        audit={},
        stories=[
            {"key": "S1", "episodes": ["D1"], "weight": "major"},
            {"key": "S2", "episodes": ["D2"], "weight": "minor"},
        ],
    )
    return StorySelection(
        carriers=carriers,
        story=story,
        episodes=[
            {"episode": "S1", "day_episodes": ["D1"]},
            {"episode": "S2", "day_episodes": ["D2"]},
        ],
        alternatives_of=alternatives_of,
        slots=4,
        calls={},
        lines={"mate": "mate's own line", "spare": "spare's own line", "far": "far's own line"},
    )


def unit(asset_id, moment, taken):
    return {"asset_id": asset_id, "moment": moment, "kind": "still", "taken": taken}


def pool_of(carriers, alternatives_of, event_units, anchor_label):
    return alternatives_pool(selection(carriers, alternatives_of), event_units, anchor_label)


def test_context_comes_from_the_pool_units_own_moment():
    refused = {
        "asset_id": "held",
        "event": "F01",
        "anchor": "A1",
        "chapter": 1,
        "why": "a Live Photo of the poetry book",
        "story_episode": "S1",
    }
    pool = pool_of(
        [refused],
        {"held": ["mate", "spare", "far"]},
        {
            "F01": [unit("held", "M1", "2024-06-01T09:00"), unit("mate", "M1", "2024-06-01T09:04")],
            "F02": [unit("spare", "M2", "2024-06-01T11:00")],
            "F03": [unit("far", "M3", "2024-06-03T11:00")],
        },
        {"F01": "A1", "F02": "A2", "F03": "A3"},
    )

    rows = {row["asset_id"]: row for row in pool(refused)}

    assert rows["mate"]["event"] == "F01" and rows["mate"]["anchor"] == "A1"
    assert rows["spare"]["event"] == "F02" and rows["spare"]["anchor"] == "A2"
    assert rows["far"]["event"] == "F03" and rows["far"]["anchor"] == "A3"
    # The far spare belongs to the second story: it takes that story's chapter, not the
    # refused carrier's, and its own line replaces a borrowed description.
    assert rows["far"]["chapter"] == 2 and rows["mate"]["chapter"] == 1
    assert rows["spare"]["line"] == "spare's own line"


def test_a_pool_unit_never_carries_the_refused_carriers_why():
    refused = {"asset_id": "held", "event": "F01", "why": "a Live Photo of the poetry book"}
    pool = pool_of(
        [refused],
        {"held": ["mate"]},
        {"F01": [unit("held", "M1", "2024-06-01T09:00"), unit("mate", "M1", "2024-06-01T09:04")]},
        {"F01": "A1"},
    )

    rows = pool(refused)

    assert rows and all("poetry" not in str(row) and "why" not in row for row in rows)
