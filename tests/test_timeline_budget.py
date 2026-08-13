"""Literal tests for the final content/title timeline contract."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.processing.assembly_config import AssemblyClip, TitleScreenSettings


def _clip(asset_id: str, date: str) -> AssemblyClip:
    return AssemblyClip(path=Path(f"/{asset_id}.mp4"), duration=5.0, asset_id=asset_id, date=date)


def _titles(
    title_duration: float = 3.5,
    divider_duration: float = 2.0,
    ending_duration: float = 7.0,
) -> TitleScreenSettings:
    return TitleScreenSettings(
        title_duration=title_duration,
        month_divider_duration=divider_duration,
        ending_duration=ending_duration,
        month_divider_threshold=1,
        divider_mode="month",
    )


def test_sixty_second_memory_keeps_eighty_percent_content() -> None:
    from immich_memories.processing.timeline_budget import plan_timeline

    clips = [
        _clip("jan", "2026-01-05"),
        _clip("feb", "2026-02-05"),
        _clip("mar", "2026-03-05"),
    ]

    plan = plan_timeline(clips, _titles(), 60.0, "monthly_highlights")

    assert plan.content_budget >= 48.0
    assert plan.title_budget <= 12.0
    assert plan.title_duration == 3.5
    assert plan.ending_duration == 7.0
    assert plan.max_dividers == 0
    assert plan.divider_policy == "pending"


def test_selected_months_receive_the_complete_divider_set() -> None:
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [
        _clip(month.lower(), f"2026-{index:02d}-05")
        for index, month in enumerate(
            ["February", "March", "April", "May", "June", "July"], start=2
        )
    ]
    titles = _titles(ending_duration=4.0)
    preliminary = plan_timeline(clips, titles, 60.0, "person_spotlight")

    final = finalize_selected_timeline(
        preliminary,
        clips,
        selected_duration=48.0,
        title_settings=titles,
        memory_type="person_spotlight",
    )

    assert preliminary.divider_policy == "pending"
    assert preliminary.max_dividers == 0
    assert final.divider_policy == "all"
    assert final.eligible_dividers == 5
    assert final.max_dividers == 5
    assert final.title_budget == pytest.approx(17.5)
    assert final.soft_max_duration == pytest.approx(70.0)


def test_selected_month_dividers_are_all_or_none_above_soft_maximum() -> None:
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [_clip(str(month), f"2026-{month:02d}-05") for month in range(2, 8)]
    titles = _titles(ending_duration=4.0)

    final = finalize_selected_timeline(
        plan_timeline(clips, titles, 60.0, "person_spotlight"),
        clips,
        selected_duration=53.0,
        title_settings=titles,
        memory_type="person_spotlight",
    )

    assert final.divider_policy == "none"
    assert final.eligible_dividers == 5
    assert final.max_dividers == 0
    assert final.title_budget == pytest.approx(7.5)


def test_single_clip_month_is_included_after_selection() -> None:
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [
        _clip("jan-1", "2026-01-05"),
        _clip("jan-2", "2026-01-15"),
        _clip("feb", "2026-02-05"),
    ]
    titles = _titles(ending_duration=4.0)
    titles.month_divider_threshold = 2

    final = finalize_selected_timeline(
        plan_timeline(clips, titles, 60.0, "person_spotlight"),
        clips,
        selected_duration=15.0,
        title_settings=titles,
        memory_type="person_spotlight",
    )

    assert final.eligible_dividers == 1
    assert final.max_dividers == 1
    assert final.divider_policy == "all"


def test_first_month_is_not_counted_as_a_divider() -> None:
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [_clip("jan", "2026-01-05"), _clip("feb", "2026-02-05")]
    titles = _titles(ending_duration=0.0)

    plan = finalize_selected_timeline(
        plan_timeline(clips, titles, 60.0, "year_in_review"),
        clips,
        selected_duration=10.0,
        title_settings=titles,
        memory_type="year_in_review",
    )

    assert plan.eligible_dividers == 1
    assert plan.max_dividers == 1
    assert plan.title_budget == pytest.approx(5.5)


def test_short_memory_shortens_opening_instead_of_stealing_content() -> None:
    from immich_memories.processing.timeline_budget import plan_timeline

    plan = plan_timeline([_clip("one", "2026-01-05")], _titles(), 15.0, "custom")

    assert plan.title_duration == 3.0
    assert plan.ending_duration == 0.0
    assert plan.content_budget == 12.0
    assert plan.soft_max_duration == 18.0


def test_disabled_titles_leave_the_full_budget_for_content() -> None:
    from immich_memories.processing.timeline_budget import plan_timeline

    settings = _titles()
    settings.enabled = False

    plan = plan_timeline([_clip("one", "2026-01-05")], settings, 60.0, "custom")

    assert plan.content_budget == 60.0
    assert plan.title_budget == 0.0
    assert plan.max_dividers == 0


def test_trip_location_changes_are_capped_by_the_same_title_budget() -> None:
    from immich_memories.processing.timeline_budget import plan_timeline

    clips = [
        AssemblyClip(
            path=Path("/brussels.mp4"),
            duration=5,
            date=datetime(2026, 7, 20, tzinfo=UTC).isoformat(),
            latitude=50.8503,
            longitude=4.3517,
            location_name="Brussels",
        ),
        AssemblyClip(
            path=Path("/paris.mp4"),
            duration=5,
            date=datetime(2026, 7, 21, tzinfo=UTC).isoformat(),
            latitude=48.8566,
            longitude=2.3522,
            location_name="Paris",
        ),
        AssemblyClip(
            path=Path("/lyon.mp4"),
            duration=5,
            date=datetime(2026, 7, 22, tzinfo=UTC).isoformat(),
            latitude=45.764,
            longitude=4.8357,
            location_name="Lyon",
        ),
    ]
    settings = _titles(title_duration=2.0, ending_duration=0.0)

    plan = plan_timeline(clips, settings, 30.0, "trip")

    assert plan.max_dividers == 2
    assert plan.title_budget == 6.0
    assert plan.divider_policy == "capped"


def test_year_dividers_keep_the_existing_capped_budget() -> None:
    from immich_memories.processing.timeline_budget import plan_timeline

    clips = [
        _clip("2024", "2024-12-05"),
        _clip("2025", "2025-01-05"),
        _clip("2026", "2026-01-05"),
    ]
    titles = _titles(title_duration=2.0, ending_duration=0.0)
    titles.divider_mode = "year"

    plan = plan_timeline(clips, titles, 30.0, "year_in_review")

    assert plan.divider_policy == "capped"
    assert plan.eligible_dividers == 2
    assert plan.max_dividers == 2
    assert plan.title_budget == 6.0
