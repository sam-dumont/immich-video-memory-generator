"""Exhaustive candidate-to-generation request contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import cast

import pytest

from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate
from immich_memories.automation.generation_request import GenerationRequest


def _candidate(
    category: CandidateCategory,
    memory_type: str,
    *,
    start: date = date(2025, 1, 1),
    end: date = date(2025, 12, 31),
    people: list[str] | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=memory_type,
        category=category,
        date_range_start=start,
        date_range_end=end,
        person_names=people or [],
        memory_key=f"key:{category.value}",
        score=0.75,
        reason="test candidate",
        asset_count=100,
    )


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (
            _candidate(
                CandidateCategory.MONTHLY_REVIEW,
                "monthly_highlights",
                start=date(2026, 5, 1),
                end=date(2026, 5, 31),
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "monthly_highlights",
                "--year",
                "2026",
                "--month",
                "5",
                "--source=auto",
                "--memory-key=key:monthly_review",
                "--memory-category=monthly_review",
            ],
        ),
        (
            _candidate(
                CandidateCategory.ACTIVITY_BURST,
                "monthly_highlights",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "monthly_highlights",
                "--year",
                "2026",
                "--month",
                "4",
                "--source=auto",
                "--memory-key=key:activity_burst",
                "--memory-category=activity_burst",
            ],
        ),
        (
            _candidate(CandidateCategory.YEAR_IN_REVIEW, "year_in_review"),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "year_in_review",
                "--year",
                "2025",
                "--source=auto",
                "--memory-key=key:year_in_review",
                "--memory-category=year_in_review",
            ],
        ),
        (
            _candidate(
                CandidateCategory.PERSON_SPOTLIGHT,
                "person_spotlight",
                people=["Alice"],
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "person_spotlight",
                "--year",
                "2025",
                "--person=Alice",
                "--source=auto",
                "--memory-key=key:person_spotlight",
                "--memory-category=person_spotlight",
            ],
        ),
        (
            _candidate(
                CandidateCategory.BIRTHDAY,
                "person_spotlight",
                start=date(2024, 3, 1),
                end=date(2025, 2, 28),
                people=["Alice"],
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "person_spotlight",
                "--year",
                "2024",
                "--birthday",
                "--person=Alice",
                "--source=auto",
                "--memory-key=key:birthday",
                "--memory-category=birthday",
            ],
        ),
        (
            _candidate(
                CandidateCategory.MULTI_PERSON,
                "multi_person",
                people=["Alice", "--Bob"],
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "multi_person",
                "--year",
                "2025",
                "--person=Alice",
                "--person=--Bob",
                "--source=auto",
                "--memory-key=key:multi_person",
                "--memory-category=multi_person",
            ],
        ),
        (
            _candidate(
                CandidateCategory.ON_THIS_DAY,
                "on_this_day",
                start=date(2026, 8, 11),
                end=date(2026, 8, 11),
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "on_this_day",
                "--source=auto",
                "--memory-key=key:on_this_day",
                "--memory-category=on_this_day",
            ],
        ),
        (
            _candidate(
                CandidateCategory.TRIP,
                "trip",
                start=date(2026, 5, 3),
                end=date(2026, 5, 11),
            ),
            [
                "immich-memories",
                "generate",
                "--memory-type",
                "trip",
                "--year",
                "2026",
                "--start",
                "2026-05-03",
                "--end",
                "2026-05-11",
                "--source=auto",
                "--memory-key=key:trip",
                "--memory-category=trip",
            ],
        ),
    ],
)
def test_category_maps_to_exact_argv(candidate: MemoryCandidate, expected: list[str]) -> None:
    """Changing or omitting a category branch changes its generated CLI contract."""
    assert GenerationRequest.from_candidate(candidate, upload=False).to_argv() == expected


def test_upload_is_an_independent_flag() -> None:
    candidate = _candidate(CandidateCategory.YEAR_IN_REVIEW, "year_in_review")

    argv = GenerationRequest.from_candidate(candidate, upload=True).to_argv()

    assert argv[-1] == "--upload-to-immich"


def test_unknown_category_fails_before_request_creation() -> None:
    candidate = _candidate(CandidateCategory.YEAR_IN_REVIEW, "year_in_review")
    unknown = replace(candidate, category=cast(CandidateCategory, "unknown"))

    with pytest.raises(ValueError, match="Unsupported automation category"):
        GenerationRequest.from_candidate(unknown, upload=False)
