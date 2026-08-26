"""Pass 2. Reduce repetition without asking the model to rank.

The pass this replaces asked which frame in a moment was its peak. Measured on
the real library at temperature 0, that question follows tile POSITION rather
than the picture in 0 of 12 cases, across widths 3-8 and fidelities 150-700px,
while returning answers that parse and carry fluent specific reasons. See
`docs/implementation-plans/2026-08-26-what-the-model-can-be-asked.md`.

So the work is split by what can actually be established:

- Arithmetic absorbs frames sharing an EXACT capture instant. Two devices on one
  moment are one moment seen twice; 558 of a dense month's 1468 candidates, at
  no model cost. Which twin ships is not an editorial question -- Thein's set
  test is "at a glance, one should not be mistaken for another", and two frames
  of one instant are not distinguishable at a glance.

- Everything else waits for a question with a referent outside the comparison.

The absorbing rule is EXACT instants and nothing wider. Two frames 7.6 seconds
apart, one place, one subject, were measured to be two different pictures of a
fast-moving event; a "within N seconds" rule merges them and the sequence is
gone. Arithmetic gets only the part it can prove.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_source import PreparedEditorialSource

SELECTS_PASS_VERSION = "pass-2-selects-v1"  # noqa: S105 - editorial pass identity


@dataclass(frozen=True)
class AbsorbedFrame:
    """One frame folded into another that shares its exact capture instant."""

    asset_id: str
    kept_asset_id: str
    reason: str


@dataclass(frozen=True)
class SelectsPassResult:
    """Chronological Pass 2 membership after provable repetition is removed."""

    survivors: tuple[EditorialCandidate, ...]
    absorbed: tuple[AbsorbedFrame, ...]
    trace: PassTrace
    warnings: tuple[str, ...] = ()


def run_selects(
    prepared: PreparedEditorialSource,
    admitted: Sequence[EditorialCandidate],
) -> SelectsPassResult:
    """Absorb frames sharing a capture instant, keeping one of each by a stated rule.

    `admitted` is what reached this pass -- Cull's survivors in the live flow --
    while the moment structure comes from the prepared source, so a frame Cull
    removed cannot absorb one it kept.

    Absorbing happens INSIDE a moment, never across the corpus. A moment is
    bounded by place as well as time, and two devices far apart at one instant
    are two people's parallel days -- measured on a real one, a racing circuit
    and a house 120km away within the same few minutes. Folding those together
    by clock alone invents a day neither of them had.
    """
    still_here = {candidate.asset_id for candidate in admitted}
    survivors: list[EditorialCandidate] = []
    absorbed: list[AbsorbedFrame] = []
    for moment in prepared.moment_groups:
        by_instant: dict[datetime, list[EditorialCandidate]] = {}
        for candidate in moment.candidates:
            if candidate.asset_id in still_here:
                by_instant.setdefault(candidate.taken_at, []).append(candidate)
        for instant in sorted(by_instant):
            together = by_instant[instant]
            kept = min(together, key=_keeping_order)
            survivors.append(kept)
            absorbed.extend(
                AbsorbedFrame(
                    asset_id=candidate.asset_id,
                    kept_asset_id=kept.asset_id,
                    reason="shares an exact capture instant with the frame that was kept",
                )
                for candidate in together
                if candidate.asset_id != kept.asset_id
            )
    survivors.sort(key=lambda candidate: (candidate.taken_at, candidate.asset_id))
    chosen = tuple(survivors)
    folded = tuple(absorbed)
    return SelectsPassResult(
        survivors=chosen,
        absorbed=folded,
        trace=_record_trace(prepared, admitted, chosen, folded),
    )


def _record_trace(
    prepared: PreparedEditorialSource,
    admitted: Sequence[EditorialCandidate],
    survivors: tuple[EditorialCandidate, ...],
    absorbed: tuple[AbsorbedFrame, ...],
) -> PassTrace:
    """Every absorbed frame names the frame it was folded into, and why.

    A bare verdict cannot be re-examined when the question changes, and this one
    is a rule rather than a judgement, so the rule has to be legible from the
    trace alone.
    """
    prepared.trace.record_editorial_pass(
        PassTrace(
            name="pass-2-selects",
            input_ids=tuple(candidate.asset_id for candidate in admitted),
            kept_ids=tuple(candidate.asset_id for candidate in survivors),
            rejected=tuple(
                TraceDecision(item.asset_id, f"{item.reason}: {item.kept_asset_id}")
                for item in absorbed
            ),
            unresolved=(),
            duration_before=sum(item.shippable_duration for item in admitted),
            duration_after=sum(item.shippable_duration for item in survivors),
            provenance=DecisionProvenance(
                pass_name="pass-2-selects",  # noqa: S106 - public editorial pass identity
                pass_version=SELECTS_PASS_VERSION,
                schema_version="none - this stage asks no model",
                model_identity="",
                input_ids=tuple(candidate.asset_id for candidate in admitted),
                sheet_hashes=(),
                request_key="",
                cache_hit=False,
            ),
        )
    )
    return prepared.trace.editorial_passes[-1]


def _keeping_order(candidate: EditorialCandidate) -> tuple[object, ...]:
    """The stated rule, in order: a favourite, then source evidence, then the ID.

    Written down rather than reasoned about, because this is deliberately not an
    editorial judgement. The ID last is only there to make the answer the same
    on every run.
    """
    return (not candidate.favourite, candidate.asset_id)
