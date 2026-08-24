"""Memory candidate detection — data models and dedup key generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class CandidateCategory(StrEnum):
    """The detector identity of a proposed memory."""

    MONTHLY_REVIEW = "monthly_review"
    ACTIVITY_BURST = "activity_burst"
    YEAR_IN_REVIEW = "year_in_review"
    PERSON_SPOTLIGHT = "person_spotlight"
    BIRTHDAY = "birthday"
    MULTI_PERSON = "multi_person"
    ON_THIS_DAY = "on_this_day"
    TRIP = "trip"
    # Named for the family rather than for the catalogue: what makes this kind
    # of proposal different is that the library volunteered it, not that it is
    # a day. A recurring subject aggregated out of the analysis corpus is the
    # same thing at another granularity and reads as this member's sibling.
    EMERGENT_DAY = "emergent_day"


@dataclass
class MemoryCandidate:
    """A proposed memory that could be generated next."""

    memory_type: str
    category: CandidateCategory
    date_range_start: date
    date_range_end: date
    person_names: list[str]
    memory_key: str
    score: float
    reason: str
    asset_count: int
    extra_params: dict[str, Any] = field(default_factory=dict)


def make_memory_key(
    memory_type: str,
    date_range_start: date,
    date_range_end: date,
    person_names: list[str] | None = None,
    discriminator: str | None = None,
) -> str:
    """Build a deterministic dedup fingerprint for a memory.

    Format: {type}:{start}:{end}:{sorted,lowered,persons}[:{discriminator}]
    Same inputs always produce the same key, regardless of person order or case.

    The discriminator is for a memory that is allowed to come back: the same
    day proposed on its tenth anniversary and again on its fifteenth is two
    memories, and without it the second could never fire. Keys already written
    carry no discriminator and no trailing colon, so every one of them still
    matches the run that wrote it.
    """
    persons = ",".join(sorted(n.lower() for n in (person_names or [])))
    suffix = f":{discriminator}" if discriminator else ""
    return (
        f"{memory_type}:{date_range_start.isoformat()}:"
        f"{date_range_end.isoformat()}:{persons}{suffix}"
    )
