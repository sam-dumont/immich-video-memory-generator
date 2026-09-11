"""The story view is read from the plan alone, in the order the memory weighs it."""

from __future__ import annotations

import json
from pathlib import Path

from immich_memories.ui.pages.memory_story_data import read_story_view, story_view_from_plan


def _plan() -> dict:
    return {
        "story": {
            "thesis": "One synthetic month, three days of test patterns.",
            "episodes": [
                {
                    "episode": "S0002",
                    "title": "The closing captures",
                    "weight": "glimpse",
                    "purpose": "Closes the month",
                    "granted": 1,
                    "day": "2024-06-22",
                },
                {
                    "key": "S0001",
                    "title": "The first captures",
                    "weight": "dominant",
                    "purpose": "Opens the month",
                },
            ],
        },
        "carriers": [
            {
                "asset_id": "photo-late",
                "kind": "still",
                "seconds": 4.0,
                "taken": "2024-06-22T14:10:00+00:00",
                "story_episode": "S0002",
                "why": "The closing captures: a still test pattern",
                "standing": "maybe",
            },
            {
                "asset_id": "video-second",
                "kind": "video",
                "seconds": 4.0,
                "taken": "2024-06-09T10:15:00+00:00",
                "story_episode": "S0001",
                "why": "The first captures: a moving test pattern",
                "standing": "remarkable",
            },
            {
                "asset_id": "photo-first",
                "kind": "still",
                "seconds": 4.0,
                "taken": "2024-06-08T10:15:00+00:00",
                "story_episode": "S0001",
                "why": "an unprefixed reason",
                "standing": "maybe",
            },
        ],
        "duration_realization": {
            "requested_seconds": 60.0,
            "content_budget_seconds": 52.0,
            "selected_content_seconds": 12.0,
            "status": "editorial_shortfall",
        },
    }


def test_stories_come_in_weight_order_with_their_carriers_in_capture_order() -> None:
    view = story_view_from_plan(_plan())

    assert view.thesis == "One synthetic month, three days of test patterns."
    assert [story.key for story in view.stories] == ["S0001", "S0002"]
    assert [carrier.asset_id for carrier in view.stories[0].carriers] == [
        "photo-first",
        "video-second",
    ]


def test_the_reason_loses_only_its_own_story_title_prefix() -> None:
    view = story_view_from_plan(_plan())

    first, second = view.stories[0].carriers
    assert first.reason == "an unprefixed reason"
    assert second.reason == "a moving test pattern"


def test_granted_and_day_fall_back_to_the_carriers_when_the_record_lacks_them() -> None:
    view = story_view_from_plan(_plan())

    opening, closing = view.stories
    assert (opening.granted, opening.day) == (2, "2024-06-08")
    assert (closing.granted, closing.day) == (1, "2024-06-22")


def test_render_mode_from_the_selection_wins_over_the_planner_kind() -> None:
    view = story_view_from_plan(_plan(), render_modes={"photo-first": "motion"})

    by_id = {carrier.asset_id: carrier for story in view.stories for carrier in story.carriers}
    assert by_id["photo-first"].render_label == "Motion"
    assert by_id["video-second"].render_label == "Motion"
    assert by_id["photo-late"].render_label == "Still"


def test_the_duration_line_reads_the_realization() -> None:
    view = story_view_from_plan(_plan())

    assert view.duration is not None
    assert view.duration.line == (
        "12 s of pictures and video selected for a 60 s memory, 52 s of it available "
        "for content — editorial shortfall"
    )
    assert view.carrier_count == 3


def test_a_plan_without_a_realization_has_no_duration_line() -> None:
    plan = _plan()
    del plan["duration_realization"]

    assert story_view_from_plan(plan).duration is None


def test_an_attempt_directory_is_read_only_when_it_holds_a_plan(tmp_path: Path) -> None:
    assert read_story_view(tmp_path) is None

    (tmp_path / "plan.private.json").write_text(json.dumps(_plan()))

    view = read_story_view(tmp_path)
    assert view is not None
    assert len(view.stories) == 2
