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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.selection_coverage import AnalysisCoverage

if TYPE_CHECKING:
    from collections.abc import Iterable

_active: ContextVar[Trace | None] = ContextVar("selection_trace", default=None)


@dataclass(frozen=True)
class Stage:
    """One decision point: what went in, what came out, and why."""

    name: str
    kept: int
    dropped: int
    favorites_in: int
    favorites_out: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Trace:
    """Everything a run decided, in order."""

    stages: list[Stage] = field(default_factory=list)
    # Not a stage: it describes the pool every stage worked on, not a step
    # the pool passed through. Re-set by each re-selection, so the value that
    # survives is the one the verify passes left behind (#489).
    coverage: AnalysisCoverage | None = None

    def record(
        self,
        name: str,
        before: Iterable,
        after: Iterable,
        reasons: list[str] | None = None,
    ) -> None:
        """Note that `name` turned `before` into `after`."""
        before_list, after_list = list(before), list(after)
        after_ids = {_asset_id(item) for item in after_list}
        lost = [item for item in before_list if _asset_id(item) not in after_ids]
        self.stages.append(
            Stage(
                name=name,
                kept=len(after_list),
                dropped=len(lost),
                favorites_in=sum(_is_favorite(i) for i in before_list),
                favorites_out=sum(_is_favorite(i) for i in after_list),
                reasons=reasons or [],
            )
        )

    def report(self) -> str:
        """The funnel, as text — widest column is where the pool went."""
        if not self.stages:
            return "No selection stages were recorded.\n"
        width = max(len(s.name) for s in self.stages)
        lines = [
            *self._coverage_lines(),
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
        return "\n".join(lines) + "\n"

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
                }
                for s in self.stages
            ],
        }


def _asset_id(item: object) -> str:
    clip = getattr(item, "clip", item)
    asset = getattr(clip, "asset", clip)
    return str(getattr(asset, "id", id(item)))


def _is_favorite(item: object) -> bool:
    clip = getattr(item, "clip", item)
    asset = getattr(clip, "asset", clip)
    return bool(getattr(asset, "is_favorite", False))


def active() -> Trace | None:
    """The trace this run is recording into, if any."""
    return _active.get()


def record(
    name: str,
    before: Iterable,
    after: Iterable,
    reasons: list[str] | None = None,
) -> None:
    """Record a stage when tracing is on; do nothing when it is not."""
    trace = _active.get()
    if trace is not None:
        trace.record(name, before, after, reasons)


def record_coverage(coverage: AnalysisCoverage) -> None:
    """Note how much of the pool a real look scored, when tracing is on."""
    trace = _active.get()
    if trace is not None:
        trace.coverage = coverage


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

    def __enter__(self) -> Trace:
        self._token = _active.set(self.trace)
        return self.trace

    def __exit__(self, *_exc: object) -> None:
        if self._token is not None:
            _active.reset(self._token)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.trace.report())
            self.path.with_suffix(".json").write_text(
                json.dumps(self.trace.as_dict(), indent=2) + "\n"
            )
