"""Stable outer lifecycle for one memory-generation operation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class OperationalPhase(StrEnum):
    """Public phases in their only valid forward order."""

    DISCOVERY = "discovery"
    DOWNLOAD = "download"
    ANALYSIS = "analysis"
    SELECTION = "selection"
    RENDER = "render"
    MUSIC = "music"
    DELIVERY = "delivery"
    COMPLETE = "complete"

    @property
    def order(self) -> int:
        """Return the stable lifecycle position used for monotonic updates."""
        return tuple(type(self)).index(self)

    @property
    def label(self) -> str:
        """Return the shared human-facing label."""
        return self.value.title()


@dataclass(frozen=True)
class PhaseEvent:
    """One observable update within an outer operational phase."""

    phase: OperationalPhase
    current: int
    total: int
    message: str
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if self.current < 0 or self.total < 0:
            raise ValueError("phase item counts cannot be negative")
        if self.total and self.current > self.total:
            raise ValueError("phase current count cannot exceed total")
        if self.elapsed_seconds < 0:
            raise ValueError("phase elapsed time cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the stable status contract."""
        return {
            "phase": self.phase.value,
            "label": self.phase.label,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "elapsed_seconds": self.elapsed_seconds,
        }
