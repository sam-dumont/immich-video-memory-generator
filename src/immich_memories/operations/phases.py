"""Stable, user-facing stages for a memory generation run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OperationalPhase(StrEnum):
    """The outer lifecycle shown consistently by CLI, UI, and status surfaces."""

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
        """Return the stable position in the outer lifecycle."""
        return list(type(self)).index(self)

    @property
    def label(self) -> str:
        """Return a compact label suitable for CLI and UI status."""
        return self.value.replace("_", " ").title()


def format_phase_progress(phase: str | OperationalPhase, message: str) -> str:
    """Render a user-visible progress message from the shared outer phase."""
    return f"{OperationalPhase(phase).label}: {message}"


@dataclass(frozen=True)
class PhaseEvent:
    """One progress update in the stable outer lifecycle."""

    phase: OperationalPhase
    current: int
    total: int
    message: str
    elapsed_seconds: float

    def to_dict(self) -> dict[str, str | int | float]:
        """Serialize the shared status payload without exposing internals."""
        return {
            "phase": self.phase.value,
            "label": self.phase.label,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "elapsed_seconds": self.elapsed_seconds,
        }
