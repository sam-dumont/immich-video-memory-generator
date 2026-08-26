"""Immutable records exchanged between editorial selection passes."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from immich_memories.analysis.strict_json import is_safe_model_text

if TYPE_CHECKING:
    from immich_memories.api.models import Asset

RECORD_SHOT_FUNCTION_MAX_CHARS = 48
RECORD_SHOT_REASON_MAX_CHARS = 96
LIVE_PHOTO_RENDERING_FAMILY_VERSION = "live-photo-rendering-family-v1"
# The request shows these to the model and the parser demands exactly them.
# Naming them once is what stops a prose prompt drifting from a strict reader.
RECORD_SHOT_WIRE_KEYS = ("tile", "function", "reason")
CULL_REJECT_WIRE_KEYS = ("tile", "defect", "evidence")


@dataclass(frozen=True)
class EditorialCandidate:
    """One source-eligible visual before any editorial decision has happened."""

    asset_id: str
    taken_at: datetime
    media_kind: Literal["photo", "video", "live_photo"]
    live_photo_stitch_member_ids: tuple[str, ...]
    rendering_family_id: str | None
    favourite: bool
    source: Asset
    proposed_segment: tuple[float, float] | None
    shippable_duration: float
    grounded_annotations: tuple[str, ...]


@dataclass(frozen=True)
class LivePhotoRenderingFamily:
    """Source-owned aligned options for one later still-or-motion rendering choice."""

    family_id: str
    still_ids: tuple[str, ...]
    video_ids: tuple[str, ...]
    trim_points: tuple[tuple[float, float], ...]
    shutter_timestamps: tuple[float, ...]
    motion_duration_seconds: float | None = None
    minimum_motion_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_rendering_family_ids(self)
        _validate_rendering_family_timing(self)
        _validate_rendering_family_durations(self)
        expected = live_photo_rendering_family_id(
            self.still_ids,
            self.video_ids,
            self.trim_points,
            self.shutter_timestamps,
            motion_duration_seconds=self.motion_duration_seconds,
            minimum_motion_seconds=self.minimum_motion_seconds,
        )
        if self.family_id != expected:
            raise ValueError("Live Photo rendering family ID must hash its canonical manifest")


def _validate_rendering_family_ids(family: LivePhotoRenderingFamily) -> None:
    size = len(family.still_ids)
    aligned_sizes = (
        len(family.video_ids),
        len(family.trim_points),
        len(family.shutter_timestamps),
    )
    unique = len(set(family.still_ids)) == size == len(set(family.video_ids))
    nonblank = all(value.strip() for value in (*family.still_ids, *family.video_ids))
    if size == 0 or any(item != size for item in aligned_sizes) or not unique or not nonblank:
        raise ValueError("Live Photo rendering family needs unique aligned source IDs")


def _validate_rendering_family_timing(family: LivePhotoRenderingFamily) -> None:
    trims_are_safe = all(
        _is_finite_number(start) and _is_finite_number(end) and start >= 0 and end > start
        for start, end in family.trim_points
    )
    shutters_are_safe = all(_is_finite_number(value) for value in family.shutter_timestamps)
    if not trims_are_safe or not shutters_are_safe:
        raise ValueError("Live Photo rendering family needs safe aligned timing")
    order = tuple(zip(family.shutter_timestamps, family.still_ids, strict=True))
    if order != tuple(sorted(order)):
        raise ValueError("Live Photo rendering family entries must be chronological")


def _validate_rendering_family_durations(family: LivePhotoRenderingFamily) -> None:
    durations = (family.motion_duration_seconds, family.minimum_motion_seconds)
    paired = (durations[0] is None) == (durations[1] is None)
    safe = all(value is None or (_is_finite_number(value) and value > 0) for value in durations)
    if not paired or not safe:
        raise ValueError("Live Photo rendering duration and minimum must be safe and paired")


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def live_photo_rendering_family_id(
    still_ids: tuple[str, ...],
    video_ids: tuple[str, ...],
    trim_points: tuple[tuple[float, float], ...],
    shutter_timestamps: tuple[float, ...],
    *,
    motion_duration_seconds: float | None,
    minimum_motion_seconds: float | None,
) -> str:
    """Hash the versioned aligned rendering manifest without choosing a render mode."""
    manifest = {
        "version": LIVE_PHOTO_RENDERING_FAMILY_VERSION,
        "entries": [
            {
                "still_id": still_id,
                "video_id": video_id,
                "trim_point": [start, end],
                "shutter_timestamp": timestamp,
            }
            for still_id, video_id, (start, end), timestamp in zip(
                still_ids,
                video_ids,
                trim_points,
                shutter_timestamps,
                strict=True,
            )
        ],
        "motion_duration_seconds": motion_duration_seconds,
        "minimum_motion_seconds": minimum_motion_seconds,
    }
    digest = sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return f"{LIVE_PHOTO_RENDERING_FAMILY_VERSION}-{digest}"


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
            or not is_safe_model_text(
                self.function,
                max_chars=RECORD_SHOT_FUNCTION_MAX_CHARS,
            )
            or not is_safe_model_text(
                self.reason,
                max_chars=RECORD_SHOT_REASON_MAX_CHARS,
            )
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
