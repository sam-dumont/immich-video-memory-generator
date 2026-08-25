"""Immutable records exchanged between editorial selection passes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionProvenance:
    """Evidence that lets a decision be reproduced from its original request."""

    pass_name: str
    pass_version: str
    schema_version: str
    model_identity: str
    input_ids: tuple[str, ...]
    sheet_hashes: tuple[str, ...]
    request_key: str
    cache_hit: bool


@dataclass(frozen=True)
class TraceDecision:
    """One asset's named editorial fate."""

    asset_id: str
    reason: str


@dataclass(frozen=True)
class ConservationCheck:
    """Whether an editorial pass gave every input exactly one fate."""

    valid: bool
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    unexpected_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassTrace:
    """The immutable record of one editorial pass."""

    name: str
    input_ids: tuple[str, ...]
    kept_ids: tuple[str, ...]
    rejected: tuple[TraceDecision, ...]
    unresolved: tuple[TraceDecision, ...]
    duration_before: float
    duration_after: float
    provenance: DecisionProvenance
    request_traces: tuple[RequestTrace, ...] = ()
    conservation: ConservationCheck | None = None


@dataclass(frozen=True)
class RequestTrace:
    """The request provenance associated with one editorial pass."""

    provenance: DecisionProvenance
    attached_sheet_hashes: tuple[str, ...]
    planned_calls: int = 1
    actual_calls: int = 0
    cache_hit: bool = False
    tile_count: int = 0
    provider: str = ""
    model: str = ""
    attempts: tuple[RequestAttemptTrace, ...] = ()
    original_provenance: DecisionProvenance | None = None


@dataclass(frozen=True)
class RequestAttemptTrace:
    """One wire attempt, including failures and request dialect adjustments."""

    attempt: int
    outcome: str
    status_code: int | None = None
    adaptation: str | None = None
