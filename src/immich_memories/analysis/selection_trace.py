"""A record of how a selection was reached, stage by stage.

Selection passes a pool through a dozen filters, caps, scalers and LLM
judgements, and until now the only way to see what happened was to grep the
log and infer. That is slow and it is wrong often enough to matter: a real
February started with 38 favorites and shipped none, and finding the stage
that ate them took several rounds of guesswork.

A stage records what it received and what it let through. The report then
shows the funnel — including how many favorites survive each step, which is
the question most often asked of it.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    ConservationCheck,
    DecisionProvenance,
    PassTrace,
    RequestTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_coverage import AnalysisCoverage

if TYPE_CHECKING:
    from collections.abc import Iterable

_active: ContextVar[Trace | None] = ContextVar("selection_trace", default=None)


# How many dropped clips to name per stage before summarising the rest.
_ACCOUNT_LIMIT = 12


# A trace is complete in JSON. Markdown names a useful sample and points to it.
_EDITORIAL_DECISION_LIMIT = 12


@dataclass(frozen=True)
class ClipStory:
    """What became of one clip, and where."""

    asset_id: str
    facts: str
    shipped: bool
    survived: tuple[str, ...]
    dropped_at: str | None
    admitted_at: str | None
    # Why the stage that dropped it did so, when that stage said.
    reason: str | None = None
    first_pass: str | None = None


@dataclass(frozen=True)
class Stage:
    """One decision point: what went in, what came out, and why."""

    name: str
    kept: int
    dropped: int
    favorites_in: int
    favorites_out: int
    reasons: list[str] = field(default_factory=list)
    # Which ones. record() already works these out to count them; keeping
    # them is what turns a funnel into an account of each clip.
    kept_ids: tuple[str, ...] = ()
    lost_ids: tuple[str, ...] = ()
    gained_ids: tuple[str, ...] = ()
    # Per-clip reasons, where the stage had one to give. `reasons` above is
    # the stage's own narration; this is what it said about a named clip, and
    # it is what the account can answer "why is that not in there" with.
    notes: dict[str, str] = field(default_factory=dict)


@dataclass
class Trace:
    """Everything a run decided, in order."""

    stages: list[Stage] = field(default_factory=list)
    # Moments that shipped something else while their favourite was dropped.
    # None means nothing checked; an empty list means the law held.
    lost_favourites: list | None = None
    # What each clip was, keyed by asset id, so a story can name it.
    clips: dict[str, str] = field(default_factory=dict)
    # Not a stage: it describes the pool every stage worked on, not a step
    # the pool passed through. Re-set by each re-selection, so the value that
    # survives is the one the verify passes left behind (#489).
    coverage: AnalysisCoverage | None = None
    # Things a reader must not miss, printed above the funnel. A pass that
    # could not run is the one this exists for: "0 dropped" has meant both
    # "approved" and "never answered" for as long as the review has existed.
    warnings: list[str] = field(default_factory=list)
    # New editorial passes sit beside the legacy stage adapter until Task 14.
    editorial_passes: list[PassTrace] = field(default_factory=list)
    requests: list[RequestTrace] = field(default_factory=list)

    def record_request(self, request_trace: RequestTrace) -> None:
        """Record one planned visual request and every wire outcome it caused."""
        self.requests.append(request_trace)

    def record_editorial_pass(self, pass_trace: PassTrace) -> None:
        """Record one immutable editorial pass and its conservation result."""
        conservation = _check_conservation(pass_trace)
        recorded = replace(pass_trace, conservation=conservation)
        self.editorial_passes.append(recorded)
        if not conservation.valid:
            self.warnings.append(f"conservation failure in {pass_trace.name}")

    def record(
        self,
        name: str,
        before: Iterable,
        after: Iterable,
        reasons: list[str] | None = None,
        notes: dict[str, str] | None = None,
    ) -> None:
        """Note that `name` turned `before` into `after`."""
        before_list, after_list = list(before), list(after)
        before_ids = {_asset_id(item) for item in before_list}
        kept_ids = tuple(_asset_id(item) for item in after_list)
        after_id_set = set(kept_ids)
        lost = [item for item in before_list if _asset_id(item) not in after_id_set]
        self._remember(before_list)
        self._remember(after_list)
        self.stages.append(
            Stage(
                name=name,
                kept=len(after_list),
                dropped=len(lost),
                favorites_in=sum(_is_favorite(i) for i in before_list),
                favorites_out=sum(_is_favorite(i) for i in after_list),
                reasons=reasons or [],
                notes=dict(notes or {}),
                kept_ids=kept_ids,
                lost_ids=tuple(_asset_id(i) for i in lost),
                gained_ids=tuple(asset_id for asset_id in kept_ids if asset_id not in before_ids),
            )
        )

    def _remember(self, items: list) -> None:
        """Keep what each clip is, so the account can name it.

        Refreshed at every sighting, not snapshotted at the first. A clip can
        be looked at long after it enters the cut — the verify pass describes
        what the pool could not — and a first-sight snapshot showed a carrier
        as category-less after the look that categorised it. That artifact was
        read as evidence the label never arrived.
        """
        for item in items:
            self.clips[_asset_id(item)] = _facts_of(item)

    def story_of(self, asset_id: str) -> ClipStory:
        """Everything this run decided about one clip, in order."""
        survived: list[str] = []
        dropped_at: str | None = None
        admitted_at: str | None = None
        reason: str | None = None
        first_pass = next(
            (
                pass_trace.name
                for pass_trace in self.editorial_passes
                if pass_trace.name != "source-eligibility" and asset_id in pass_trace.input_ids
            ),
            None,
        )
        for pass_trace in self.editorial_passes:
            rejected = next(
                (decision for decision in pass_trace.rejected if decision.asset_id == asset_id),
                None,
            )
            if rejected is not None:
                dropped_at = pass_trace.name
                reason = rejected.reason
                survived = []
            elif asset_id in pass_trace.kept_ids:
                admitted_at = pass_trace.name
        for stage in self.stages:
            if asset_id in stage.gained_ids:
                admitted_at = stage.name
            if asset_id in stage.lost_ids:
                dropped_at = stage.name
                reason = stage.notes.get(asset_id)
                survived = []
            elif asset_id in stage.kept_ids:
                survived.append(stage.name)
        shipped = bool(self.stages) and asset_id in self.stages[-1].kept_ids
        return ClipStory(
            asset_id=asset_id,
            facts=self.clips.get(asset_id, ""),
            shipped=shipped,
            survived=tuple(survived),
            dropped_at=None if shipped else dropped_at,
            admitted_at=admitted_at,
            reason=None if shipped else reason,
            first_pass=first_pass,
        )

    def _favourite_law_lines(self) -> list[str]:
        """Whether any moment shipped a neighbour of the photograph it starred."""
        if self.lost_favourites is None:
            return []
        if not self.lost_favourites:
            return ["favourites law: held — no moment shipped over its favourite", ""]
        lines = [
            f"favourites law: BROKEN in {len(self.lost_favourites)} moment(s) — "
            "a favourite was dropped and a neighbour shipped",
        ]
        for lost in self.lost_favourites[:5]:
            lines.append(
                f"    dropped {', '.join(lost.favourites)} · shipped {', '.join(lost.shipped)}"
            )
        return [*lines, ""]

    def report(self) -> str:
        """The funnel, as text — widest column is where the pool went."""
        if not self.stages and not self.editorial_passes:
            return "No selection stages were recorded.\n"
        width = max((len(s.name) for s in self.stages), default=len("stage"))
        lines = [
            *self._warning_lines(),
            *self._coverage_lines(),
            *self._favourite_law_lines(),
            f"{'stage'.ljust(width)}  {'kept':>5} {'lost':>5}  {'favorites':>12}",
            "",
        ]
        for stage in self.stages:
            favorites = f"{stage.favorites_in} -> {stage.favorites_out}"
            marker = (
                "  <-- all favorites lost here"
                if (stage.favorites_in and not stage.favorites_out)
                else ""
            )
            lines.append(
                f"{stage.name.ljust(width)}  {stage.kept:>5} {stage.dropped:>5}  "
                f"{favorites:>12}{marker}"
            )
            lines.extend(f"{' ' * width}    - {reason}" for reason in stage.reasons)
        return "\n".join([*lines, *self._editorial_pass_lines(), *self._account_lines()]) + "\n"

    def _editorial_pass_lines(self) -> list[str]:
        if not self.editorial_passes:
            return []
        lines = ["", "editorial passes", "-" * 16]
        for pass_trace in self.editorial_passes:
            decisions = [*pass_trace.rejected, *pass_trace.unresolved]
            lines.append(
                f"  {pass_trace.name}: {len(pass_trace.kept_ids)} kept, "
                f"{len(pass_trace.rejected)} rejected, {len(pass_trace.unresolved)} unresolved"
            )
            lines.extend(
                f"      {decision.asset_id} — {decision.reason}"
                for decision in decisions[:_EDITORIAL_DECISION_LIMIT]
            )
            if len(decisions) > _EDITORIAL_DECISION_LIMIT:
                lines.append(
                    f"      showing {_EDITORIAL_DECISION_LIMIT} of {len(decisions)}; "
                    "full list in JSON"
                )
        return lines

    def _account_lines(self) -> list[str]:
        """Why each clip is in the cut, and why the rest are not."""
        if not self.stages:
            return []
        return [*self._shipped_lines(), *self._rejected_lines()]

    def _shipped_lines(self) -> list[str]:
        lines = ["", "why each clip is in the cut", "-" * 27]
        shipped = [self.story_of(asset_id) for asset_id in self.stages[-1].kept_ids]
        for story in sorted(shipped, key=lambda s: s.facts):
            lines.append(f"  {story.facts}")
            if story.admitted_at:
                lines.append(f"      admitted by: {story.admitted_at}")
            if story.survived:
                lines.append(f"      survived: {', '.join(story.survived)}")
        return lines

    def _rejected_lines(self) -> list[str]:
        by_stage: dict[str, list[str]] = {}
        for asset_id in {i for stage in self.stages for i in stage.lost_ids}:
            story = self.story_of(asset_id)
            if not story.shipped and story.dropped_at is not None:
                said = f"{story.facts}  — {story.reason}" if story.reason else story.facts
                by_stage.setdefault(story.dropped_at, []).append(said)
        if not by_stage:
            return []
        lines = ["", "and why the rest are not", "-" * 24]
        for stage in (s.name for s in self.stages):
            lines += _dropped_at(stage, sorted(by_stage.pop(stage, [])))
        return lines

    def _warning_lines(self) -> list[str]:
        """What a reader must see before anything else in the report."""
        if not self.warnings:
            return []
        return [*(f"!! {warning}" for warning in self.warnings), ""]

    def _coverage_lines(self) -> list[str]:
        """The pool's coverage, above the funnel — it frames everything below."""
        if self.coverage is None:
            return []
        return [
            f"pool coverage: {self.coverage.analyzed} of {self.coverage.total} "
            f"candidates ({self.coverage.percent}%) were visually analyzed",
            "",
        ]

    def as_dict(self) -> dict:
        return {
            "warnings": self.warnings.copy(),
            "coverage": (
                None
                if self.coverage is None
                else {"analyzed": self.coverage.analyzed, "total": self.coverage.total}
            ),
            "stages": [
                {
                    "name": s.name,
                    "kept": s.kept,
                    "dropped": s.dropped,
                    "favorites_in": s.favorites_in,
                    "favorites_out": s.favorites_out,
                    "reasons": s.reasons,
                    "notes": s.notes,
                    "kept_ids": list(s.kept_ids),
                    "lost_ids": list(s.lost_ids),
                    "gained_ids": list(s.gained_ids),
                }
                for s in self.stages
            ],
            "editorial_passes": [
                _editorial_pass_dict(pass_trace) for pass_trace in self.editorial_passes
            ],
            "requests": [_request_dict(request) for request in self.requests],
            "clips": self.clips,
        }


def _check_conservation(pass_trace: PassTrace) -> ConservationCheck:
    fates = (
        *pass_trace.kept_ids,
        *(decision.asset_id for decision in pass_trace.rejected),
        *(decision.asset_id for decision in pass_trace.unresolved),
    )
    fate_counts: dict[str, int] = {}
    for asset_id in fates:
        fate_counts[asset_id] = fate_counts.get(asset_id, 0) + 1
    input_ids = _unique_ids(pass_trace.input_ids)
    missing_ids = tuple(asset_id for asset_id in input_ids if asset_id not in fate_counts)
    duplicate_ids = tuple(asset_id for asset_id in input_ids if fate_counts.get(asset_id, 0) > 1)
    unexpected_ids = tuple(
        asset_id for asset_id in _unique_ids(fates) if asset_id not in set(input_ids)
    )
    return ConservationCheck(
        valid=not missing_ids and not duplicate_ids and not unexpected_ids,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        unexpected_ids=unexpected_ids,
    )


def _unique_ids(asset_ids: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for asset_id in asset_ids:
        if asset_id not in seen:
            unique.append(asset_id)
            seen.add(asset_id)
    return tuple(unique)


def _editorial_pass_dict(pass_trace: PassTrace) -> dict:
    conservation = pass_trace.conservation or _check_conservation(pass_trace)
    return {
        "name": pass_trace.name,
        "input_ids": list(pass_trace.input_ids),
        "kept_ids": list(pass_trace.kept_ids),
        "rejected": [_decision_dict(decision) for decision in pass_trace.rejected],
        "unresolved": [_decision_dict(decision) for decision in pass_trace.unresolved],
        "duration_before": pass_trace.duration_before,
        "duration_after": pass_trace.duration_after,
        "provenance": _provenance_dict(pass_trace.provenance),
        "request_traces": [_request_dict(request) for request in pass_trace.request_traces],
        "conservation": {
            "valid": conservation.valid,
            "missing_ids": list(conservation.missing_ids),
            "duplicate_ids": list(conservation.duplicate_ids),
            "unexpected_ids": list(conservation.unexpected_ids),
        },
    }


def _decision_dict(decision: TraceDecision) -> dict:
    return {"asset_id": decision.asset_id, "reason": decision.reason}


def _request_dict(request: RequestTrace) -> dict:
    return {
        "provenance": _provenance_dict(request.provenance),
        "attached_sheet_hashes": list(request.attached_sheet_hashes),
        "planned_calls": request.planned_calls,
        "actual_calls": request.actual_calls,
        "cache_hit": request.cache_hit,
        "tile_count": request.tile_count,
        "provider": request.provider,
        "model": request.model,
        "attempts": [
            {
                "attempt": attempt.attempt,
                "outcome": attempt.outcome,
                "status_code": attempt.status_code,
                "adaptation": attempt.adaptation,
            }
            for attempt in request.attempts
        ],
        "original_provenance": (
            None
            if request.original_provenance is None
            else _provenance_dict(request.original_provenance)
        ),
    }


def _provenance_dict(provenance: DecisionProvenance) -> dict:
    return {
        "pass_name": provenance.pass_name,
        "pass_version": provenance.pass_version,
        "schema_version": provenance.schema_version,
        "model_identity": provenance.model_identity,
        "input_ids": list(provenance.input_ids),
        "sheet_hashes": list(provenance.sheet_hashes),
        "request_key": provenance.request_key,
        "cache_hit": provenance.cache_hit,
    }


def _asset_id(item: object) -> str:
    clip = getattr(item, "clip", item)
    asset = getattr(clip, "asset", clip)
    return str(getattr(asset, "id", id(item)))


def _dropped_at(stage: str, dropped: list[str]) -> list[str]:
    """One stage's rejects, named up to the limit and then counted."""
    if not dropped:
        return []
    lines = [f"  dropped at {stage} ({len(dropped)}):"]
    lines += [f"      {facts}" for facts in dropped[:_ACCOUNT_LIMIT]]
    if len(dropped) > _ACCOUNT_LIMIT:
        lines.append(f"      ... and {len(dropped) - _ACCOUNT_LIMIT} more")
    return lines


def _facts_of(item: object) -> str:
    """One line saying what a clip is, for the account.

    The account is diagnostic. It may describe a candidate poorly; it may
    never take a run down with it — and now that the pool's own stages record,
    it sees candidates far earlier and from more call sites than the funnel
    ever did.
    """
    try:
        return _described(item)
    except Exception:  # WHY broad: any field of any shape, and none worth a crash
        return _asset_id(item)[:8] or "an unreadable candidate"


def _described(item: object) -> str:
    clip = getattr(item, "clip", item)
    asset = getattr(clip, "asset", clip)
    when = getattr(asset, "file_created_at", None)
    bits = [
        str(getattr(asset, "original_file_name", "") or _asset_id(item)[:8]),
        when.isoformat(timespec="minutes") if when else "undated",
        f"score={getattr(item, 'score', 0.0):.2f}",
    ]
    if getattr(asset, "is_favorite", False):
        bits.append("starred")
    for label, source, attr in (
        ("", clip, "llm_category"),
        ("interest=", clip, "llm_interestingness"),
    ):
        value = getattr(source, attr, None)
        if value is not None:
            bits.append(f"{label}{value:g}" if isinstance(value, float) else f"{label}{value}")
    return "  ".join(str(bit) for bit in bits)


def _is_favorite(item: object) -> bool:
    clip = getattr(item, "clip", item)
    asset = getattr(clip, "asset", clip)
    return bool(getattr(asset, "is_favorite", False))


def path_from_env() -> Path | None:
    """Where this run should write its trace, if the caller asked for one.

    Read here rather than at each call site because two layers now open a
    context — the CLI runner, so the pool's own filters are on the record, and
    the pipeline, so a UI run still traces — and they must agree on the file.
    """
    import os

    configured = os.environ.get("IMMICH_MEMORIES_SELECTION_TRACE")
    return Path(configured) if configured else None


def active() -> Trace | None:
    """The trace this run is recording into, if any."""
    return _active.get()


def record(
    name: str | PassTrace,
    before: Iterable | None = None,
    after: Iterable | None = None,
    reasons: list[str] | None = None,
    notes: dict[str, str] | None = None,
) -> None:
    """Record a legacy stage or editorial pass when tracing is on."""
    trace = _active.get()
    if trace is None:
        return
    if isinstance(name, PassTrace):
        trace.record_editorial_pass(name)
        return
    if before is None or after is None:
        raise TypeError("legacy selection stages require both before and after candidates")
    trace.record(name, before, after, reasons, notes)


def warn(message: str) -> None:
    """Put something above the funnel that a reader must not miss."""
    trace = _active.get()
    if trace is not None:
        trace.warnings.append(message)


def record_coverage(coverage: AnalysisCoverage) -> None:
    """Note how much of the pool a real look scored, when tracing is on."""
    trace = _active.get()
    if trace is not None:
        trace.coverage = coverage


def record_favourite_law(pool: Iterable, selected: Iterable) -> None:
    """Check the one rule selection is not allowed to break, when tracing is on.

    Measured against the finished cut rather than asserted mid-funnel: a
    favourite may legitimately be dropped by one stage and restored by
    backfill, and only the end of the run knows what actually shipped.
    """
    trace = _active.get()
    if trace is None:
        return
    from immich_memories.analysis.favourite_law import moments_that_lost_their_favourite

    trace.lost_favourites = moments_that_lost_their_favourite(
        [_asset_of(item) for item in pool],
        {_asset_id(item) for item in selected},
    )


def _asset_of(item: object) -> object:
    clip = getattr(item, "clip", None)
    return getattr(clip, "asset", None) if clip is not None else item


class tracing:  # noqa: N801 - reads as a context manager at the call site
    """Collect a trace for the duration of a selection, then write it out.

    A ContextVar rather than a threaded argument: the stages that make these
    decisions sit four layers apart, and giving a dozen functions a recorder
    parameter to carry data none of them use would obscure the code this is
    meant to explain.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.trace = Trace()
        self._token: Token[Trace | None] | None = None
        self._joined = False

    def __enter__(self) -> Trace:
        """Start a trace, or join the one already running.

        The pipeline opens a context of its own, and stages that run before it
        — the source-quality drop, the subject policy — can only be recorded
        by a caller opening one earlier. Two independent traces would put those
        stages in a report nobody writes out, so the inner context joins the
        outer instead: one trace, one file, whichever layer asked first.
        """
        already = _active.get()
        if already is not None:
            self.trace = already
            self._joined = True
            return already
        self._token = _active.set(self.trace)
        return self.trace

    def __exit__(self, *_exc: object) -> None:
        if self._joined:
            return
        if self._token is not None:
            _active.reset(self._token)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.trace.report())
            self.path.with_suffix(".json").write_text(
                json.dumps(self.trace.as_dict(), indent=2) + "\n"
            )
