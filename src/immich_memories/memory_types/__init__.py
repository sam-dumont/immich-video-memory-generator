"""Memory type presets and factories.

Public API:
- MemoryType: Enum of all supported memory types
- MemoryPreset, PersonFilter: Configuration dataclasses
- create_preset, list_memory_types: Factory functions
- build_season, build_month, build_on_this_day: Date range builders
"""

from immich_memories.memory_types.date_builders import (
    build_month,
    build_on_this_day,
    build_season,
)
from immich_memories.memory_types.factory import create_preset, list_memory_types
from immich_memories.memory_types.presets import (
    MemoryPreset,
    PersonFilter,
    person_filter_for,
)
from immich_memories.memory_types.registry import MemoryType

__all__ = [
    "MemoryPreset",
    "MemoryType",
    "PersonFilter",
    "build_month",
    "build_on_this_day",
    "build_season",
    "create_preset",
    "list_memory_types",
    "person_filter_for",
]
