"""Pool outcomes read from the same saved evidence as ``runs why``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from immich_memories.analysis.selection_trace import Trace
from immich_memories.operations.reader_words import stage_words
from immich_memories.operations.storyboard import TRACE_FILE, Storyboard, read_storyboard


def read_trace(attempt_dir: Path) -> Trace | None:
    """Read the decision log beside the plan; older runs may have none."""
    path = attempt_dir / TRACE_FILE
    return Trace.from_dict(json.loads(path.read_text())) if path.is_file() else None


def _trace_outcomes(trace: Trace) -> dict[str, str]:
    outcomes = dict.fromkeys(trace.clips, "Outcome not recorded for this cut")
    for stage in trace.editorial_passes:
        for asset_id in stage.input_ids:
            outcomes.setdefault(asset_id, "Outcome not recorded for this cut")
        for decision in stage.rejected:
            reason = decision.reason or "No reason recorded"
            outcomes[decision.asset_id] = f"Left out at {stage_words(stage.name)}: {reason}"
        for decision in stage.unresolved:
            outcomes[decision.asset_id] = (
                f"Undecided at {stage_words(stage.name)}: {decision.reason}"
            )
    return outcomes


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
            return cls(None, None, {}, "No cut recorded yet")
        board, trace = read_storyboard(attempt_dir), read_trace(attempt_dir)
        outcomes = _trace_outcomes(trace) if trace else {}
        for shot in board.shots if board else ():
            reason = f": {shot.reason}" if shot.reason else ""
            outcomes[shot.asset_id] = f"In the cut at {shot.timecode}{reason}"
        fallback = "Not in this cut's pool" if trace else "Outcome not recorded for this cut"
        return cls(board, trace, outcomes, fallback)

    def describe(self, asset_id: str) -> str:
        """Explain this picture's final outcome without inferring it from current ticks."""
        return self.outcomes.get(asset_id, self.fallback)
