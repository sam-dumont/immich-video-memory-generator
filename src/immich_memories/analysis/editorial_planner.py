"""Small production seam for an editorial planner to choose the finished cut."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from immich_memories.analysis.selection_trace import Trace
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

__all__ = ["EditorialPlan", "EditorialPlanner", "EditorialSelection"]


@dataclass(frozen=True)
class EditorialSelection:
    """One selected source and any final-cut rendering decision attached to it."""

    asset_id: str
    start_time: float | None = None
    end_time: float | None = None
    render_mode: Literal["motion", "still"] | None = None
    render_frame_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("editorial selection asset ID cannot be blank")
        for name, value in (
            ("start time", self.start_time),
            ("end time", self.end_time),
            ("render frame", self.render_frame_seconds),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"editorial selection {name} must be finite and nonnegative")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("editorial selection needs a forward segment")
        if self.render_mode not in (None, "motion", "still"):
            raise ValueError("editorial selection render mode must be motion or still")
        if self.render_frame_seconds is not None and self.render_mode != "still":
            raise ValueError("an editorial render frame requires still mode")


@dataclass(frozen=True)
class EditorialPlan:
    """An ordered selection, or a reason editorial planning was unavailable."""

    selections: tuple[EditorialSelection, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.selections, tuple) or any(
            not isinstance(selection, EditorialSelection) for selection in self.selections
        ):
            raise ValueError("editorial plan selections must be an immutable typed tuple")
        if self.unavailable_reason is not None and (
            not isinstance(self.unavailable_reason, str) or not self.unavailable_reason.strip()
        ):
            raise ValueError("editorial plan unavailable reason cannot be blank")
        if self.selections and self.unavailable_reason is not None:
            raise ValueError("editorial plan cannot be both available and unavailable")

    @property
    def selected_asset_ids(self) -> tuple[str, ...]:
        return tuple(selection.asset_id for selection in self.selections)


class EditorialPlanner(Protocol):
    """Select a finished cut from the pipeline's canonical candidates."""

    def plan(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        trace: Trace,
    ) -> EditorialPlan: ...
