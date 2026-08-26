"""Immutable records exchanged between editorial selection passes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from immich_memories.api.models import Asset

RECORD_SHOT_FUNCTION_MAX_CHARS = 48
RECORD_SHOT_REASON_MAX_CHARS = 96


@dataclass(frozen=True)
class EditorialCandidate:
    """One source-eligible visual before any editorial decision has happened."""

    asset_id: str
    taken_at: datetime
    media_kind: Literal["photo", "video", "live_photo"]
    favourite: bool
    source: Asset
    proposed_segment: tuple[float, float] | None
    shippable_duration: float
    grounded_annotations: tuple[str, ...]


@dataclass(frozen=True)
class SourceEvidence:
    """Precomputed source measurements supplied without running new analysis."""

    blur: float | None = None
    exposure: float | None = None
    similarity: str | None = None


@dataclass(frozen=True)
class InsightEvidence:
    """One period observation grounded in the episodes and pixels that support it."""

    observation: str
    episode_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.observation.strip():
            raise ValueError("insight evidence observation cannot be blank")
        if not self.episode_ids or any(not episode_id.strip() for episode_id in self.episode_ids):
            raise ValueError("insight evidence needs nonblank episode IDs")
        if not self.asset_ids or any(not asset_id.strip() for asset_id in self.asset_ids):
            raise ValueError("insight evidence needs nonblank asset IDs")


@dataclass(frozen=True)
class PeriodInsight:
    """The provisional reading of one complete period wall."""

    thesis: str | None
    evidence: tuple[InsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]
    unavailable_reason: str | None
    revision: int
    provenance: DecisionProvenance

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("period insight revision cannot be negative")
        if (self.thesis is None) == (self.unavailable_reason is None):
            raise ValueError("period insight needs exactly one thesis or unavailable reason")
        if self.thesis is not None and (not self.thesis.strip() or not self.evidence):
            raise ValueError("period insight thesis needs visual evidence")
        if self.unavailable_reason is not None and not self.unavailable_reason.strip():
            raise ValueError("period insight unavailable reason cannot be blank")
        if any(not item.episode_ids or not item.asset_ids for item in self.evidence):
            raise ValueError("period insight evidence must identify episodes and assets")


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
class RecordShotMark:
    """A kept visual whose factual function must remain independently auditable."""

    asset_id: str
    function: str
    reason: str

    def __post_init__(self) -> None:
        if (
            not self.asset_id.strip()
            or not self.function.strip()
            or len(self.function) > RECORD_SHOT_FUNCTION_MAX_CHARS
            or not self.reason.strip()
            or len(self.reason) > RECORD_SHOT_REASON_MAX_CHARS
        ):
            raise ValueError("record-shot mark needs a stable asset and bounded function/reason")


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
    record_shots: tuple[RecordShotMark, ...] = ()


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
