"""The storyboard is the cut in shot order, read from the plan and the projection alone."""

from __future__ import annotations

import json
from pathlib import Path

from immich_memories.ui.pages.memory_storyboard import read_storyboard, storyboard_from_plan


def _plan() -> dict:
    return {
        "story": {
            "thesis": "One synthetic month, three days of test patterns.",
            "episodes": [
                {"episode": "S0002", "title": "The closing captures", "weight": "glimpse"},
                {"key": "S0001", "title": "The first captures", "weight": "dominant"},
            ],
        },
        "carriers": [
            {
                "asset_id": "photo-late",
                "kind": "still",
                "seconds": 4.0,
                "taken": "2024-07-22T14:10:00+00:00",
                "story_episode": "S0002",
                "depicted_moment": "M9",
            },
            {
                "asset_id": "video-second",
                "kind": "video",
                "seconds": 4.0,
                "taken": "2024-06-09T10:15:00+00:00",
                "story_episode": "S0001",
                "depicted_moment": "M2",
            },
            {
                "asset_id": "photo-first",
                "kind": "still",
                "seconds": 4.0,
                "taken": "2024-06-08T10:15:00+00:00",
                "story_episode": "S0001",
                "depicted_moment": "M1",
            },
        ],
        "content_seconds": 12.0,
    }


def _projection() -> dict:
    return {
        "format": "editorial-source-rendering-v1",
        "intervals": {"video-second": [3.0, 8.5], "photo-first": [0.0, 4.0], "photo-late": [0, 4]},
    }


def test_shots_come_in_capture_order_and_keep_their_story_and_moment() -> None:
    board = storyboard_from_plan(_plan(), _projection())

    assert [shot.asset_id for shot in board.shots] == ["photo-first", "video-second", "photo-late"]
    assert [shot.story_title for shot in board.shots] == [
        "The first captures",
        "The first captures",
        "The closing captures",
    ]
    assert [shot.moment for shot in board.shots] == ["M1", "M2", "M9"]


def test_running_timecodes_add_up_to_the_planned_content() -> None:
    board = storyboard_from_plan(_plan(), _projection())

    # The projection's interval is what the renderer holds, so it wins over the carrier's seconds.
    assert [shot.seconds for shot in board.shots] == [4.0, 5.5, 4.0]
    assert [shot.start for shot in board.shots] == [0.0, 4.0, 9.5]
    assert board.total_seconds == 13.5
    assert board.shots[-1].timecode == "0:09"


def test_without_a_projection_the_carrier_seconds_are_the_shot_length() -> None:
    board = storyboard_from_plan(_plan(), None)

    assert board.total_seconds == 12.0 == _plan()["content_seconds"]


def test_a_day_change_inside_a_story_and_a_month_change_are_both_marked() -> None:
    board = storyboard_from_plan(_plan(), _projection())

    first, second, third = board.shots
    assert (first.day, first.new_day) == ("2024-06-08", True)
    assert (second.day, second.new_day) == ("2024-06-09", True)
    # Same story, new day: the loose grouping shows as a day that changed under one title.
    assert second.story_title == first.story_title
    assert [shot.chapter for shot in board.shots] == ["June 2024", "", "July 2024"]


def test_an_attempt_directory_is_read_only_when_it_holds_a_plan(tmp_path: Path) -> None:
    assert read_storyboard(tmp_path) is None

    (tmp_path / "plan.private.json").write_text(json.dumps(_plan()))
    board = read_storyboard(tmp_path)
    assert board is not None and len(board.shots) == 3

    (tmp_path / "render-projection.private.json").write_text(json.dumps(_projection()))
    board = read_storyboard(tmp_path)
    assert board is not None and board.total_seconds == 13.5


def _timed_plan() -> dict:
    """A plan the way a certified run writes it: with the timeline it will be rendered to."""
    return _plan() | {
        "render_timing": {
            "policy": {"transition": "smart", "transition_duration": 0.5},
            "timeline": {
                "target_duration": 30.0,
                "content_budget": 6.75,
                "title_budget": 10.5,
                "title_duration": 3.5,
                "ending_duration": 7.0,
                "divider_duration": 2.0,
                "max_dividers": 0,
            },
            "source_ids": ["photo-first", "video-second", "photo-late"],
            "sha256": "the reader never re-binds, so it never checks this",
        },
        "duration_realization": {
            "content_budget_seconds": 6.75,
            "selected_content_seconds": 13.5,
        },
    }


def test_shots_are_timed_and_placed_the_way_the_renderer_will_play_them() -> None:
    """The renderer trims the content to its budget and the opening card plays first."""
    board = storyboard_from_plan(_timed_plan(), _projection())

    assert [shot.seconds for shot in board.shots] == [2.0, 2.75, 2.0]
    assert [shot.start for shot in board.shots] == [3.5, 5.5, 8.25]
    assert board.shots[-1].timecode == "0:08"
    assert board.summary_label == "3 pictures, 0:06 of pictures and video, about 0:15 of film"
