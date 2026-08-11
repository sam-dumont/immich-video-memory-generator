"""Pure cadence and rotation rules for automatic memory candidates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.tracking.models import RunMetadata, normalize_memory_people


@dataclass(frozen=True)
class RejectedCandidate:
    """A candidate excluded by one stable variety rule."""

    candidate: MemoryCandidate
    rule: str


@dataclass(frozen=True)
class VarietyDecision:
    """Eligible candidates and explainable hard-rule rejections."""

    eligible: list[MemoryCandidate]
    rejected: list[RejectedCandidate]


def apply_variety_rules(
    candidates: list[MemoryCandidate],
    recent_auto_runs: list[RunMetadata],
    today: date,
) -> VarietyDecision:
    """Reject repetitive automatic candidates without weakening constraints."""
    previous = recent_auto_runs[0] if recent_auto_runs else None
    category_counts = Counter(run.memory_category for run in recent_auto_runs)
    eligible: list[MemoryCandidate] = []
    rejected: list[RejectedCandidate] = []

    for candidate in candidates:
        rule = rejection_rule(
            candidate,
            previous,
            category_counts,
            recent_auto_runs,
            today,
        )
        if rule is None:
            eligible.append(candidate)
        else:
            rejected.append(RejectedCandidate(candidate=candidate, rule=rule))

    return VarietyDecision(eligible=eligible, rejected=rejected)


def rejection_rule(
    candidate: MemoryCandidate,
    previous: RunMetadata | None,
    category_counts: Counter[str | None],
    recent_auto_runs: list[RunMetadata],
    today: date,
) -> str | None:
    if previous is not None and previous.memory_category == candidate.category.value:
        return "same_category_as_previous"
    if category_counts[candidate.category.value] >= 2:
        return "category_limit_two_of_six"
    if candidate.category is CandidateCategory.MONTHLY_REVIEW and any(
        run.memory_category == CandidateCategory.MONTHLY_REVIEW.value
        and (run.completed_at or run.created_at).year == today.year
        and (run.completed_at or run.created_at).month == today.month
        for run in recent_auto_runs
    ):
        return "monthly_review_already_completed_this_month"
    person_runs = [run for run in recent_auto_runs if run.memory_people][:2]
    recent_people = {person for run in person_runs for person in run.memory_people}
    if set(normalize_memory_people(candidate.person_names)) & recent_people:
        return "person_in_last_two_person_runs"
    return None
