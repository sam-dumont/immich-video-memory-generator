"""Pool outcomes read from the same saved evidence as ``runs why``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from immich_memories.analysis.selection_trace import Trace
from immich_memories.operations.reader_words import stage_words
from immich_memories.operations.storyboard import TRACE_FILE, Storyboard, read_storyboard

_UNRECORDED = "Outcome not recorded for this cut"

# What the planner's own stage means when it drops a picture no pass objected to.
_NOT_IN_PLAN = "kept by every pass, not used in the plan"


def read_trace(attempt_dir: Path) -> Trace | None:
    """Read the decision log beside the plan; older runs may have none."""
    path = attempt_dir / TRACE_FILE
    return Trace.from_dict(json.loads(path.read_text())) if path.is_file() else None


def _trace_outcomes(trace: Trace) -> dict[str, str]:
    """Every outcome the saved log carries, later decisions overriding earlier ones.

    This is a second walk over the same evidence as ``Trace.story_of``, which
    answers for one asset at a time and in the terminal's words. Folding them
    into one derivation is worth doing and is deliberately not done here.
    """
    outcomes = dict.fromkeys(trace.clips, _UNRECORDED)
    _pass_outcomes(trace, outcomes)
    _stage_outcomes(trace, outcomes)
    return outcomes


def _pass_outcomes(trace: Trace, outcomes: dict[str, str]) -> None:
    for pass_trace in trace.editorial_passes:
        for asset_id in pass_trace.input_ids:
            outcomes.setdefault(asset_id, _UNRECORDED)
        for decision in pass_trace.rejected:
            reason = decision.reason or "No reason recorded"
            outcomes[decision.asset_id] = f"Left out at {stage_words(pass_trace.name)}: {reason}"
        for decision in pass_trace.unresolved:
            outcomes[decision.asset_id] = (
                f"Undecided at {stage_words(pass_trace.name)}: {decision.reason}"
            )


def _stage_outcomes(trace: Trace, outcomes: dict[str, str]) -> None:
    """Name the stage that dropped a picture no editorial pass objected to.

    The last stage is the plan itself: everything it did not keep is out, and
    the pictures every pass kept land here with no pass to name. Pictures a
    pass already rejected are in these `lost_ids` too, and keep that pass's
    reason: the stage is only counting the same drop one level down.
    """
    final = trace.stages[-1].name if trace.stages else None
    for stage in trace.stages:
        for asset_id in stage.lost_ids:
            if outcomes.get(asset_id, _UNRECORDED) != _UNRECORDED:
                continue
            unstated = _NOT_IN_PLAN if stage.name == final else "No reason recorded"
            reason = stage.notes.get(asset_id) or unstated
            outcomes[asset_id] = f"Left out at {stage_words(stage.name)}: {reason}"


@dataclass(frozen=True)
class CandidateFates:
    board: Storyboard | None
    trace: Trace | None
    outcomes: dict[str, str]
    fallback: str

    @classmethod
    def read(cls, attempt_dir: Path | None) -> CandidateFates:
        """Take one snapshot per pool page, keeping ticks independent of saved decisions."""
        if attempt_dir is None:
            # Before the first cut there is nothing to say about any picture.
            # A placeholder repeated under every thumbnail in the pool is not
            # nothing: it is a line claiming evidence is missing when none is due.
            return cls(None, None, {}, "")
        board, trace = read_storyboard(attempt_dir), read_trace(attempt_dir)
        outcomes = _trace_outcomes(trace) if trace else {}
        for shot in board.shots if board else ():
            reason = f": {shot.reason}" if shot.reason else ""
            outcomes[shot.asset_id] = f"In the cut at {shot.timecode}{reason}"
        fallback = "Not in this cut's pool" if trace else _UNRECORDED
        return cls(board, trace, outcomes, fallback)

    def describe(self, asset_id: str) -> str:
        """This picture's final outcome, never inferred from the current ticks.

        Empty when no cut has been made yet: there is no outcome to report and
        no label to hold a place for.
        """
        return self.outcomes.get(asset_id, self.fallback)
