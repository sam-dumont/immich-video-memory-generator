"""Reject-only Pass 1 over banked episode scans and retained atlas pixels."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import starmap

from immich_memories.analysis.cull_answer import CullDecision
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    RequestTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.cache.editorial_verdicts import EditorialVerdicts
from immich_memories.store.episode_readings import EpisodeReadingProducer

_REVIEW_STATE_COLOURS = {
    "KEEP": (45, 65, 75),
    "RECORD": (0, 115, 150),
    "CULL": (170, 20, 20),
}
_REVIEW_FAVOURITE_COLOUR = (180, 130, 0)


# Scopes a remembered verdict to what the buckets MEANT. Re-rendering a picture
# must not clear a judgement about what it is; redefining `notes` must.
# v2: the foreign bucket joined the question; v3: watch faces named as screens
# (both 2026-09-01) — a remembered verdict answered a different question and
# must not replay against it.
CULL_PASS_VERSION = "pass-1-cull-v3"  # noqa: S105 - editorial pass identity


def cull_pass_version(reading: EpisodeReadingProducer) -> str:
    """The bank key for verdicts this reading produced.

    The laws above are only half the question; the reading that answers them is
    the other half. #1041 bumped the episode prompt and left the constant
    alone, so one catalogued day's pictures carried the old reading's 61
    rejects AND the new reading's 36 at the same time, and a seven-carrier cut
    became three (#1059). Composing the reading's identity in retires the old
    rows instead of unioning with them, and it does so for every input the
    reading is made of: prompt, model, schema, the annotation renderer and the
    annotation producers behind its evidence.
    """
    return f"{CULL_PASS_VERSION}/{reading.prompt_version}/{reading.key()[:16]}"


@dataclass(frozen=True)
class CullDecisionResult:
    """Pass 1 policy result independent of how its evidence was transported."""

    survivors: tuple[EditorialCandidate, ...]
    rejected: tuple[CullDecision, ...]
    warnings: tuple[str, ...]
    trace: PassTrace
    actual_calls: int = 0


def run_cull_decisions(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
    *,
    provenance: DecisionProvenance,
    warnings: tuple[str, ...] = (),
    request_traces: tuple[RequestTrace, ...] = (),
    verdicts: EditorialVerdicts | None = None,
    actual_calls: int = 0,
) -> CullDecisionResult:
    """Apply the existing Cull laws to provider-neutral typed decisions.

    The bank is read and written under `provenance.pass_version`, so the key
    the trace publishes is the key the rows live under and there is nowhere for
    the two to drift apart. Production composes it with `cull_pass_version`.
    """
    # What a picture IS does not change between memories, so a verdict reached
    # once stands for all of them. What it sat beside does change, which is why
    # only the two removing buckets are remembered.
    rejects, remembered_warnings = _with_remembered_verdicts(
        prepared, rejects, verdicts, provenance.pass_version
    )
    pass_one_warnings = _ordered_unique((*warnings, *remembered_warnings))
    rejects, favourite_warnings = _protect_favourites(prepared, rejects)
    pass_one_warnings = _ordered_unique((*pass_one_warnings, *favourite_warnings))
    ordered_rejects, survivors = _apply_cull(prepared, rejects)
    pass_one_warnings = _with_over_cull_warning(
        pass_one_warnings,
        len(ordered_rejects),
        len(prepared.candidates),
    )
    _record_new_warnings(prepared, pass_one_warnings)
    recorded_trace = _record_cull_trace(
        prepared,
        provenance,
        survivors,
        ordered_rejects,
        request_traces,
    )
    if verdicts is not None:
        verdicts.remember(
            ((item.asset_id, item.bucket) for item in ordered_rejects),
            pass_version=provenance.pass_version,
        )
    authoritative_warnings = _authoritative_warnings(prepared)
    return CullDecisionResult(
        survivors,
        ordered_rejects,
        authoritative_warnings,
        recorded_trace,
        actual_calls,
    )


def _apply_cull(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
) -> tuple[tuple[CullDecision, ...], tuple[EditorialCandidate, ...]]:
    order = {candidate.asset_id: index for index, candidate in enumerate(prepared.candidates)}
    ordered_rejects = tuple(sorted(rejects, key=lambda item: order[item.asset_id]))
    rejected_ids = {decision.asset_id for decision in ordered_rejects}
    survivors = tuple(
        candidate for candidate in prepared.candidates if candidate.asset_id not in rejected_ids
    )
    return ordered_rejects, survivors


def _with_remembered_verdicts(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
    verdicts: EditorialVerdicts | None,
    pass_version: str,
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    """This run's rejects, plus the standing verdicts about the same pictures."""
    if verdicts is None:
        return rejects, ()
    decided = {decision.asset_id for decision in rejects}
    remembered = verdicts.recall(prepared.candidate_ids, pass_version=pass_version)
    added = tuple(
        starmap(
            CullDecision,
            (pair for pair in remembered.items() if pair[0] not in decided),
        )
    )
    warnings = tuple(
        f"remembered verdict culled {decision.asset_id}: {decision.reason}" for decision in added
    )
    return (*rejects, *added), warnings


def _protect_favourites(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    favourite_ids = {candidate.asset_id for candidate in prepared.candidates if candidate.favourite}
    required_ids = set(prepared.owner_required_asset_ids)
    protected_ids = favourite_ids | required_ids
    protected = tuple(decision for decision in rejects if decision.asset_id in protected_ids)
    accepted = tuple(decision for decision in rejects if decision.asset_id not in protected_ids)
    warnings = tuple(
        f"!! cull reject conflicted with protected "
        f"{'favourite' if decision.asset_id in favourite_ids else 'owner required picture'}: "
        f"{decision.asset_id}"
        for decision in protected
    )
    return accepted, warnings


def _with_over_cull_warning(
    warnings: tuple[str, ...],
    reject_count: int,
    candidate_count: int,
) -> tuple[str, ...]:
    if reject_count * 4 > candidate_count * 3:
        return (*warnings, "!! possible over-cull")
    return warnings


def _record_new_warnings(
    prepared: PreparedEditorialSource,
    warnings: tuple[str, ...],
) -> None:
    prepared.trace.warnings.extend(
        warning for warning in warnings if warning not in prepared.trace.warnings
    )


def _authoritative_warnings(prepared: PreparedEditorialSource) -> tuple[str, ...]:
    """Every note the trace holds, with only the failures carrying `!!`.

    This used to force `!!` onto all of them, which made one meaning do two
    jobs. `!!` says the sheet is invalid for an owner verdict; a recalled
    verdict is the durable bank working, and stamping INVALID on a warm run
    left the only accumulating cache in the engine reporting every success as
    a defect. Something worth saying out loud is not the same as something
    that voids the page.
    """
    return _ordered_unique(tuple(prepared.trace.warnings))


def _record_cull_trace(
    prepared: PreparedEditorialSource,
    provenance: DecisionProvenance,
    survivors: tuple[EditorialCandidate, ...],
    rejects: tuple[CullDecision, ...],
    requests: tuple[RequestTrace, ...],
) -> PassTrace:
    prepared.trace.record_editorial_pass(
        PassTrace(
            name="pass-1-cull",
            input_ids=prepared.candidate_ids,
            kept_ids=tuple(candidate.asset_id for candidate in survivors),
            rejected=tuple(TraceDecision(item.asset_id, item.reason) for item in rejects),
            unresolved=(),
            duration_before=sum(item.shippable_duration for item in prepared.candidates),
            duration_after=sum(item.shippable_duration for item in survivors),
            provenance=provenance,
            request_traces=requests,
        )
    )
    return prepared.trace.editorial_passes[-1]


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
