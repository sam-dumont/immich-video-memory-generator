"""Memory candidate detection — data models and dedup key generation."""

from __future__ import annotations

from datetime import date


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
