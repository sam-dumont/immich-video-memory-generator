"""Memory candidate detection — data models and dedup key generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class MemoryCandidate:
    """A proposed memory that could be generated next."""

    memory_type: str
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
) -> str:
    """Build a deterministic dedup fingerprint for a memory.

    Format: {type}:{start}:{end}:{sorted,lowered,persons}
    Same inputs always produce the same key, regardless of person order or case.
    """
    persons = ",".join(sorted(n.lower() for n in (person_names or [])))
    return f"{memory_type}:{date_range_start.isoformat()}:{date_range_end.isoformat()}:{persons}"
