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


def test_yearly_timeline_allows_the_complete_month_divider_set() -> None:
    """The generic 10s cushion must not impose a five-divider ceiling on years."""
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [_clip(str(month), f"2026-{month:02d}-05") for month in range(1, 9)]
    titles = _titles(ending_duration=4.0)

    final = finalize_selected_timeline(
        plan_timeline(clips, titles, 180.0, "person_spotlight"),
        clips,
        selected_duration=168.8,
        title_settings=titles,
        memory_type="person_spotlight",
    )

    assert final.divider_policy == "all"
    assert final.eligible_dividers == 7
    assert final.max_dividers == 7
    assert final.title_budget == pytest.approx(21.5)
    assert final.soft_max_duration == pytest.approx(194.0)


def test_smart_transition_overlap_is_added_to_the_selection_budget() -> None:
    """Selection must replace time that smart crossfades remove at assembly."""
    from immich_memories.processing.timeline_budget import plan_timeline

    clips = [_clip(str(index), "2026-07-05") for index in range(40)]

    plan = plan_timeline(
        clips,
        _titles(ending_duration=4.0),
        150.0,
        "custom",
        expected_clip_duration=5.0,
        transition_mode="smart",
        transition_duration=0.5,
    )

    assert plan.transition_budget > 0.0
    assert plan.content_budget + plan.title_budget - plan.transition_budget == pytest.approx(150.0)


def test_yearly_divider_gate_accounts_for_transition_overlap() -> None:
    """Raw title time may cross the ceiling even though the assembled cut fits."""
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    clips = [_clip(str(month), f"2026-{month:02d}-05") for month in range(1, 9)]
    clips.extend(_clip(f"extra-{index}", "2026-08-06") for index in range(19))
    titles = _titles(ending_duration=4.0)

    final = finalize_selected_timeline(
        plan_timeline(
            clips,
            titles,
            180.0,
            "person_spotlight",
            transition_mode="smart",
            transition_duration=0.5,
        ),
        clips,
        selected_duration=173.5,
        title_settings=titles,
        memory_type="person_spotlight",
        transition_mode="smart",
        transition_duration=0.5,
    )

    assert final.divider_policy == "all"
    assert final.max_dividers == 7
    assert final.transition_budget == pytest.approx(12.25)
    assert 173.5 + final.title_budget - final.transition_budget <= final.soft_max_duration


def test_trip_location_budget_is_resolved_from_selected_clips() -> None:
    """Candidate-pool travel must not reserve cards absent from the final cut."""
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

    candidates = [
        AssemblyClip(
            path=Path(f"/{city}.mp4"),
            duration=5.0,
            date=f"2026-07-{day:02d}",
            latitude=latitude,
            longitude=longitude,
            location_name=city,
        )
        for day, city, latitude, longitude in [
            (1, "Brussels", 50.8503, 4.3517),
            (2, "Paris", 48.8566, 2.3522),
            (3, "Lyon", 45.7640, 4.8357),
        ]
    ]
    titles = _titles(ending_duration=4.0)
    preliminary = plan_timeline(candidates, titles, 150.0, "trip")

    final = finalize_selected_timeline(
        preliminary,
        candidates[:2],
        selected_duration=10.0,
        title_settings=titles,
        memory_type="trip",
    )

    assert preliminary.divider_policy == "pending"
    assert preliminary.max_dividers == 0
    assert final.divider_policy == "capped"
    assert final.eligible_dividers == 1
    assert final.max_dividers == 1
    assert final.title_budget == pytest.approx(9.5)


def test_preview_estimate_applies_the_final_content_trim() -> None:
    from immich_memories.cli._generation_preview import GenerationPreview
    from immich_memories.processing.output_canvas import OutputCanvas
    from immich_memories.processing.timeline_budget import TimelinePlan

    preview = GenerationPreview(
        memory_type="trip",
        date_range="Jul 25 - Aug 05",
        video_candidates=1,
        live_photo_candidates=0,
        photo_candidates=0,
        selected_videos=1,
        selected_photos=0,
        selected_duration=152.1,
        timeline=TimelinePlan(
            target_duration=150.0,
            content_budget=150.3,
            title_budget=9.5,
            title_duration=3.5,
            ending_duration=4.0,
            divider_duration=2.0,
            max_dividers=1,
            transition_budget=9.8,
        ),
        canvas=OutputCanvas(width=1280, height=720, orientation="landscape"),
        output_path=Path("/trip.mp4"),
        upload_intent=False,
        music_policy="none",
    )

    assert preview.estimated_final_duration == pytest.approx(150.0)


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
    from immich_memories.processing.timeline_budget import (
        finalize_selected_timeline,
        plan_timeline,
    )

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

    preliminary = plan_timeline(clips, settings, 30.0, "trip")
    plan = finalize_selected_timeline(
        preliminary,
        clips,
        selected_duration=15.0,
        title_settings=settings,
        memory_type="trip",
    )

    assert preliminary.divider_policy == "pending"
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


def _then_and_now_titles() -> TitleScreenSettings:
    titles = _titles()
    titles.divider_mode = "year"
    titles.memory_type = "then_and_now"
    return titles


def test_a_then_and_now_budgets_a_card_for_each_era() -> None:
    """Both eras are the subject, so both get named.

    The year divider budget is len(years) - 1, because on a memory that runs
    continuously through its years the title card already names the one it
    opens on. A then-and-now's title names its two ends as a pair, not the year
    the first block belongs to, so the older era went unlabeled.
    """
    from immich_memories.processing.timeline_budget import plan_timeline

    clips = [_clip("old", "2016-05-05"), _clip("new", "2026-05-05")]

    plan = plan_timeline(clips, _then_and_now_titles(), 45.0, "then_and_now")

    assert plan.eligible_dividers == 2


def test_a_continuous_multi_year_memory_still_skips_its_opening_year() -> None:
    """The rule this exception is carved out of, kept honest."""
    from immich_memories.processing.timeline_budget import plan_timeline

    titles = _titles()
    titles.divider_mode = "year"
    clips = [_clip("a", "2024-05-05"), _clip("b", "2025-05-05"), _clip("c", "2026-05-05")]

    plan = plan_timeline(clips, titles, 120.0, "year_in_review")

    assert plan.eligible_dividers == 2
