"""Memory presets — dataclasses for scoring, filtering, and preset config."""

from __future__ import annotations

from dataclasses import dataclass, field

from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange


@dataclass
class ScoringProfile:
    """Weights for video clip scoring across different dimensions."""

    face_weight: float = 0.4
    motion_weight: float = 0.25
    stability_weight: float = 0.2
    audio_weight: float = 0.15
    content_weight: float = 0.0
    duration_weight: float = 0.15

    def to_dict(self) -> dict[str, float]:
        """Return scoring weights as a plain dictionary."""
        return {
            "face_weight": self.face_weight,
            "motion_weight": self.motion_weight,
            "stability_weight": self.stability_weight,
            "audio_weight": self.audio_weight,
            "content_weight": self.content_weight,
            "duration_weight": self.duration_weight,
        }


@dataclass
class PersonFilter:
    """Filter configuration for person-based memory types."""

    mode: str = "any"  # "any" | "all_of" | "single" | "none_of"
    person_names: list[str] = field(default_factory=list)
    require_co_occurrence: bool = False


@dataclass
class MemoryPreset:
    """Full preset configuration for a memory type."""

    memory_type: MemoryType
    name: str
    description: str
    date_ranges: list[DateRange]
    person_filter: PersonFilter
    scoring: ScoringProfile
    title_template: str
    subtitle_template: str | None = None
    default_duration_minutes: int | None = None
