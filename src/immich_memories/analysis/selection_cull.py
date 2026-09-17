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
from immich_memories.cache.editorial_verdicts import KEPT_VERDICT, EditorialVerdicts
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
    judged_asset_ids: tuple[str, ...] = (),
    actual_calls: int = 0,
) -> CullDecisionResult:
    """Apply the existing Cull laws to provider-neutral typed decisions.

    The bank is read and written under `provenance.pass_version`, so the key
    the trace publishes is the key the rows live under and there is nowhere for
    the two to drift apart. Production composes it with `cull_pass_version`.

    `judged_asset_ids` names the pictures this reading actually looked at, which
    is what makes a keep sayable: an episode that failed to read leaves its
    pictures unasked rather than kept.
    """
    # Held before `rejects` is rebound, because what goes into the bank is what
    # THIS reading said, not the union of it with what the bank already held.
    reading = rejects
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
            _reading_answer(prepared, reading, judged_asset_ids),
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
            (
                (asset_id, bucket)
                for asset_id, bucket in remembered.items()
                if asset_id not in decided and bucket != KEPT_VERDICT
            ),
        )
    )
    warnings = tuple(
        f"remembered verdict culled {decision.asset_id}: {decision.reason}" for decision in added
    )
    return (*rejects, *added), warnings


def _reading_answer(
    prepared: PreparedEditorialSource,
    reading: tuple[CullDecision, ...],
    judged_asset_ids: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """What this reading said about each picture it judged, its keeps included.

    The bank used to take the pass's rejects and nothing else, so a standing
    verdict could never be withdrawn and the survivor pool could only shrink
    (#1059). Writing the keeps down lets a later reading of the same question
    replace an answer it no longer gives. A star or an owner tick is an
    override rather than an answer about the picture, so what it saved is left
    exactly as the bank already had it.
    """
    protected = _protected_ids(prepared)
    candidates = set(prepared.candidate_ids)
    rejected = {decision.asset_id: decision.bucket for decision in reading}
    kept = tuple(
        asset_id
        for asset_id in dict.fromkeys(judged_asset_ids)
        if asset_id in candidates and asset_id not in rejected
    )
    return (
        *((asset, bucket) for asset, bucket in rejected.items() if asset not in protected),
        *((asset_id, KEPT_VERDICT) for asset_id in kept if asset_id not in protected),
    )


def _protected_ids(prepared: PreparedEditorialSource) -> frozenset[str]:
    """Pictures a star or an owner tick holds in, whatever the pass decided."""
    favourite_ids = {candidate.asset_id for candidate in prepared.candidates if candidate.favourite}
    return frozenset(favourite_ids | set(prepared.owner_required_asset_ids))


def _protect_favourites(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    favourite_ids = {candidate.asset_id for candidate in prepared.candidates if candidate.favourite}
    protected_ids = _protected_ids(prepared)
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
