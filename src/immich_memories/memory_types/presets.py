"""Memory presets — what a memory type asks for before anything is fetched."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange


@dataclass
class PersonFilter:
    """Filter configuration for person-based memory types."""

    mode: str = "any"  # "any" | "all_of" | "single" | "none_of"
    person_names: list[str] = field(default_factory=list)
    require_co_occurrence: bool = False


def person_filter_for(person_names: Sequence[str] | None) -> PersonFilter:
    """The people a memory is narrowed to, whatever memory type it is.

    Several names intersect: the memory is what holds all of them, which is
    what ``--person Alice --person Bob`` has always fetched and what a
    multi-person memory has always meant. Keeping only the first name was how
    the wizard and the CLI came to disagree on the same request (#683).
    """
    names = list(person_names or [])
    if not names:
        return PersonFilter()
    if len(names) == 1:
        return PersonFilter(mode="single", person_names=names)
    return PersonFilter(mode="all_of", person_names=names, require_co_occurrence=True)


@dataclass
class MemoryPreset:
    """Full preset configuration for a memory type."""

    memory_type: MemoryType
    name: str
    description: str
    date_ranges: list[DateRange]
    person_filter: PersonFilter
    default_duration_seconds: float | None = None
