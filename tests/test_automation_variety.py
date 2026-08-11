"""Hard guardrails for automatic memory variety."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from immich_memories.automation.calendar_detectors import MonthlyDetector
from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.variety import apply_variety_rules
from immich_memories.tracking.models import RunMetadata

TODAY = date(2026, 8, 11)


def _candidate(
    category: CandidateCategory,
    *,
    people: list[str] | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=category.value,
        category=category,
        date_range_start=date(2026, 7, 1),
        date_range_end=date(2026, 7, 31),
        person_names=people or [],
        memory_key=f"{category.value}:2026-07-01:2026-07-31:",
        score=0.7,
        reason="test candidate",
        asset_count=100,
    )


def _completed(
    category: CandidateCategory,
    *,
    people: tuple[str, ...] = (),
    created_at: datetime = datetime(2026, 8, 10, 9, 0),
    completed_at: datetime | None = datetime(2026, 8, 10, 10, 0),
) -> RunMetadata:
    return RunMetadata(
        run_id=f"{category.value}-{created_at.isoformat()}",
        created_at=created_at,
        completed_at=completed_at,
        status="completed",
        memory_category=category.value,
        memory_people=people,
        source="auto",
    )


def test_same_category_cannot_repeat() -> None:
    monthly = _candidate(CandidateCategory.MONTHLY_REVIEW)

    decision = apply_variety_rules(
        [monthly],
        [_completed(CandidateCategory.MONTHLY_REVIEW)],
        TODAY,
    )

    assert decision.eligible == []
    assert decision.rejected[0].rule == "same_category_as_previous"


def test_first_matching_rule_has_stable_precedence() -> None:
    monthly = _candidate(CandidateCategory.MONTHLY_REVIEW, people=["Alice"])
    history = [
        _completed(CandidateCategory.MONTHLY_REVIEW, people=("Alice",)),
        _completed(
            CandidateCategory.MONTHLY_REVIEW,
            people=("Alice",),
            created_at=datetime(2026, 8, 9, 9, 0),
            completed_at=datetime(2026, 8, 9, 10, 0),
        ),
    ]

    decision = apply_variety_rules([monthly], history, TODAY)

    assert [item.rule for item in decision.rejected] == ["same_category_as_previous"]


def test_category_cannot_exceed_two_of_six() -> None:
    categories = [
        CandidateCategory.MONTHLY_REVIEW,
        CandidateCategory.TRIP,
        CandidateCategory.BIRTHDAY,
        CandidateCategory.TRIP,
        CandidateCategory.PERSON_SPOTLIGHT,
        CandidateCategory.ON_THIS_DAY,
    ]
    history = [
        _completed(
            category,
            created_at=datetime(2026, 8, 10 - index, 9, 0),
            completed_at=datetime(2026, 8, 10 - index, 10, 0),
        )
        for index, category in enumerate(categories)
    ]

    decision = apply_variety_rules(
        [_candidate(CandidateCategory.TRIP)],
        history,
        TODAY,
    )

    assert decision.eligible == []
    assert decision.rejected[0].rule == "category_limit_two_of_six"


def test_monthly_review_cannot_complete_twice_in_calendar_month() -> None:
    history = [
        _completed(CandidateCategory.TRIP),
        _completed(
            CandidateCategory.MONTHLY_REVIEW,
            created_at=datetime(2026, 7, 31, 23, 0),
            completed_at=datetime(2026, 8, 1, 1, 0),
        ),
    ]

    decision = apply_variety_rules(
        [_candidate(CandidateCategory.MONTHLY_REVIEW)],
        history,
        TODAY,
    )

    assert decision.eligible == []
    assert decision.rejected[0].rule == "monthly_review_already_completed_this_month"


def test_person_cannot_repeat_across_last_two_person_bearing_runs() -> None:
    history = [
        _completed(CandidateCategory.TRIP),
        _completed(CandidateCategory.PERSON_SPOTLIGHT, people=("Alice Smith",)),
        _completed(CandidateCategory.ON_THIS_DAY),
        _completed(CandidateCategory.BIRTHDAY, people=("Bob",)),
    ]

    decision = apply_variety_rules(
        [_candidate(CandidateCategory.MULTI_PERSON, people=["  ALICE\tSMITH ", "Carol"])],
        history,
        TODAY,
    )

    assert decision.eligible == []
    assert decision.rejected[0].rule == "person_in_last_two_person_runs"


def test_monthly_detector_only_proposes_latest_completed_month() -> None:
    assets = {
        "2026-07": 100,
        "2026-06": 90,
        "2026-05": 80,
        "2026-04": 70,
        "2026-03": 60,
        "2026-02": 50,
    }

    candidates = MonthlyDetector().detect(assets, [], set(), MagicMock(), TODAY)

    assert [
        (candidate.date_range_start.year, candidate.date_range_start.month)
        for candidate in candidates
    ] == [(2026, 7)]


def test_activity_burst_is_independent_from_monthly_review_cadence() -> None:
    history = [
        _completed(CandidateCategory.TRIP),
        _completed(CandidateCategory.MONTHLY_REVIEW),
    ]
    activity_burst = _candidate(CandidateCategory.ACTIVITY_BURST)

    decision = apply_variety_rules([activity_burst], history, TODAY)

    assert decision.eligible == [activity_burst]
    assert decision.rejected == []


def test_person_outside_last_two_person_runs_is_eligible() -> None:
    history = [
        _completed(CandidateCategory.PERSON_SPOTLIGHT, people=("Alice",)),
        _completed(CandidateCategory.ON_THIS_DAY),
        _completed(CandidateCategory.BIRTHDAY, people=("Bob",)),
        _completed(CandidateCategory.TRIP),
        _completed(CandidateCategory.PERSON_SPOTLIGHT, people=("Carol",)),
    ]
    carol = _candidate(CandidateCategory.MULTI_PERSON, people=["Carol", "Dani"])

    decision = apply_variety_rules([carol], history, TODAY)

    assert decision.eligible == [carol]
    assert decision.rejected == []
