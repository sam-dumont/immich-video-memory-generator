"""Turn a planner's cut into a pipeline result, and announce the work as it runs.

Everything the editorial source route needs that is not the pipeline itself:
the membership and timing a plan must satisfy before it becomes a result, and
the stage reporter that keeps a broken display callback from changing what was
decided. Kept out of smart_pipeline so the orchestrator holds phases rather than
rules.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Callable
from math import isfinite
from typing import TYPE_CHECKING

from immich_memories.operations.cut_progress import StageUpdate

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
    from immich_memories.analysis.progress import ProgressTracker
    from immich_memories.analysis.smart_pipeline import ClipWithSegment


class EditorialStageReporter:
    """Announce the current editorial stage without letting the display decide anything.

    Holds the last record so a cancellation can re-announce it, and swallows
    every display failure: a broken progress callback must not turn a finished
    editorial decision into a failed one.
    """

    def __init__(
        self, tracker: ProgressTracker, progress_callback: Callable[[dict], None] | None
    ) -> None:
        self._tracker = tracker
        self._progress_callback = progress_callback
        self.stage = StageUpdate("Preparing editorial evidence", "analysis")

    def __call__(self, update: StageUpdate | str, *, status: str = "running") -> None:
        if isinstance(update, str):
            update = StageUpdate(update)
        self.stage = update
        if self._progress_callback is None:
            return
        payload: dict = {
            "phase_label": update.stage_label,
            "current_phase": update.phase,
            "indeterminate": not update.counted,
            "status": status,
            "started_at": self._tracker.progress.start_time,
            "elapsed_seconds": self._tracker.progress.elapsed_seconds,
            "elapsed": self._tracker.format_elapsed(),
        }
        if update.counted:
            payload.update(
                progress_fraction=update.fraction,
                current_index=update.done,
                total_items=update.total,
            )
        with contextlib.suppress(Exception):
            self._progress_callback(payload)

    def repeat(self) -> None:
        """Re-announce the stage a cancellation interrupted."""
        self(self.stage)


def editorial_membership(
    plan: EditorialPlan, analyzed: list[ClipWithSegment]
) -> dict[str, ClipWithSegment]:
    """Refuse a cut that repeats an input, repeats an output, or invents one."""
    input_ids = tuple(candidate.clip.asset.id for candidate in analyzed)
    if len(input_ids) != len(set(input_ids)):
        raise ValueError("editorial source projection received duplicate input IDs")
    output_ids = tuple(item.asset_id for item in plan.selections)
    duplicate_output_ids = sorted(
        asset_id for asset_id, count in Counter(output_ids).items() if count > 1
    )
    if duplicate_output_ids:
        raise ValueError(
            "Editorial planner returned duplicate output asset IDs: "
            + ", ".join(duplicate_output_ids)
        )
    escaped_output_ids = sorted(set(output_ids) - set(input_ids))
    if escaped_output_ids:
        raise ValueError(
            "Editorial planner returned asset IDs outside the input pool: "
            + ", ".join(escaped_output_ids)
        )
    return {candidate.clip.asset.id: candidate for candidate in analyzed}


def editorial_clip_segment(
    item: EditorialSelection,
    candidate: ClipWithSegment,
    *,
    source_verified: bool,
) -> tuple[float, float]:
    """The planner's interval for one carrier, or a refusal naming that carrier."""
    start_time = candidate.start_time if item.start_time is None else item.start_time
    end_time = candidate.end_time if item.end_time is None else item.end_time
    source_duration = candidate.clip.duration_seconds
    invalid_segment = (
        not isfinite(start_time)
        or not isfinite(end_time)
        or start_time < 0
        or end_time <= start_time
        or source_duration > 0
        and end_time > source_duration
        and not (source_verified and item.render_mode == "still")
    )
    invalid_frame = item.render_frame_seconds is not None and (
        source_duration > 0 and item.render_frame_seconds >= source_duration
    )
    if invalid_segment or invalid_frame:
        raise ValueError(f"Editorial planner returned invalid source timing for {item.asset_id}")
    return start_time, end_time
