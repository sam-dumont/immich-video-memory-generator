"""Strict parsing for independent record-shot and clear-defect namespaces."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import chain
from typing import TypeGuard

from immich_memories.analysis.editorial_contracts import (
    RECORD_SHOT_FUNCTION_MAX_CHARS,
    RECORD_SHOT_REASON_MAX_CHARS,
    RecordShotMark,
)
from immich_memories.analysis.period_insight_answer import (
    EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
    EPISODE_SCAN_SCHEMA_VERSION,
    EPISODE_VISUAL_SUMMARY_MAX_CHARS,
)
from immich_memories.analysis.strict_json import final_json_object, is_safe_model_text

RECORD_REASON_MAX_CHARS = RECORD_SHOT_REASON_MAX_CHARS
RECORD_FUNCTION_MAX_CHARS = RECORD_SHOT_FUNCTION_MAX_CHARS
CULL_EVIDENCE_REASONS = {
    "accidental_capture": {
        "camera_obstructed": "the camera is visibly obstructed",
        "unintended_partial": "the frame is visibly an unintended partial capture",
        "blank_floor_ceiling": "the frame shows only a blank floor or ceiling",
    },
    "unusable_motion_blur": {
        "subject_unrecognizable": "motion blur makes the visible subject unrecognizable",
        "frame_smeared_beyond_use": "motion smears the entire frame beyond use",
    },
    "unusable_exposure": {
        "detail_lost_to_darkness": "shadow clipping erased the visible detail",
        "detail_lost_to_highlights": "highlight clipping erased the visible detail",
    },
    "corrupt_or_obscured_pixels": {
        "decode_corruption": "decode corruption destroys the visible content",
        "lens_obscured": "the lens is visibly obscured",
        "content_not_visible": "the intended visual content is not visible",
    },
}
ALLOWED_CULL_DEFECTS = frozenset(CULL_EVIDENCE_REASONS)
_RESPONSE_PLANNING_CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class CullDecision:
    """One clearly unusable visual defect grounded in visible pixels."""

    asset_id: str
    defect: str
    evidence: str
    reason: str = field(init=False)

    def __post_init__(self) -> None:
        reason = CULL_EVIDENCE_REASONS.get(self.defect, {}).get(self.evidence)
        if not self.asset_id.strip() or reason is None:
            raise ValueError("Cull decision needs a stable asset and matching defect evidence")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True)
class ParsedCullNamespaces:
    """Independently validated Pass 1 decisions from one banked scan."""

    record_shots: tuple[RecordShotMark, ...]
    cull_rejects: tuple[CullDecision, ...]
    warnings: tuple[str, ...]
    record_valid: bool
    cull_valid: bool


def fused_episode_response_fits(
    displayed_by_episode: tuple[tuple[int, ...], ...],
    *,
    max_output_tokens: int,
) -> bool:
    """Whether the maximum valid fused v3 response fits its output envelope."""
    readings = tuple(
        {
            "episode": episode_alias,
            "page": 1,
            "visual_summary": "s" * EPISODE_VISUAL_SUMMARY_MAX_CHARS,
            "representative_tiles": displayed,
            "representative_reason": "r" * EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
        }
        for episode_alias, displayed in enumerate(displayed_by_episode, start=1)
    )
    tiles = tuple(chain.from_iterable(displayed_by_episode))
    record_shots: list[dict[str, object]] = []
    cull_rejects: list[dict[str, object]] = []
    for tile in tiles:
        record_member = {
            "tile": tile,
            "function": "f" * RECORD_FUNCTION_MAX_CHARS,
            "reason": "r" * RECORD_REASON_MAX_CHARS,
        }
        cull_member = {
            "tile": tile,
            "defect": "corrupt_or_obscured_pixels",
            "evidence": "content_not_visible",
        }
        record_json = json.dumps(record_member, separators=(",", ":"), ensure_ascii=True)
        cull_json = json.dumps(cull_member, separators=(",", ":"), ensure_ascii=True)
        (record_shots if len(record_json) > len(cull_json) else cull_rejects).append(
            record_member if len(record_json) > len(cull_json) else cull_member
        )
    envelope = json.dumps(
        {
            "schema_version": EPISODE_SCAN_SCHEMA_VERSION,
            "pack": 1,
            "episode_readings": readings,
            "record_shots": record_shots,
            "cull_rejects": cull_rejects,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return len(envelope) <= max_output_tokens * _RESPONSE_PLANNING_CHARS_PER_TOKEN


def read_cull_namespaces(
    raw: str,
    *,
    pack_alias: int,
    tile_map: Mapping[int, str],
) -> ParsedCullNamespaces | None:
    """Read Pass 1 namespaces without repairing a malformed outer response."""
    payload = final_json_object(raw)
    if (
        payload is None
        or payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION
        or not _is_integer_alias(payload.get("pack"))
        or payload.get("pack") != pack_alias
    ):
        return None
    record_shots = _read_record_shots(payload.get("record_shots"), tile_map)
    cull_rejects = _read_cull_rejects(payload.get("cull_rejects"), tile_map)
    record_valid = record_shots is not None
    cull_valid = cull_rejects is not None
    valid_records = record_shots or ()
    valid_rejects = cull_rejects or ()
    record_ids = {mark.asset_id for mark in valid_records}
    collisions = tuple(
        decision.asset_id for decision in valid_rejects if decision.asset_id in record_ids
    )
    return ParsedCullNamespaces(
        record_shots=valid_records,
        cull_rejects=tuple(
            decision for decision in valid_rejects if decision.asset_id not in record_ids
        ),
        warnings=tuple(
            f"!! cull reject conflicted with record-shot mark: {asset_id}"
            for asset_id in collisions
        ),
        record_valid=record_valid,
        cull_valid=cull_valid,
    )


def _read_record_shots(
    value: object,
    tile_map: Mapping[int, str],
) -> tuple[RecordShotMark, ...] | None:
    if not isinstance(value, list):
        return None
    parsed: list[RecordShotMark] = []
    keys: list[int] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"tile", "function", "reason"}:
            return None
        key = item.get("tile")
        function = item.get("function")
        reason = item.get("reason")
        if (
            not _is_integer_alias(key)
            or key not in tile_map
            or not is_safe_model_text(function, max_chars=RECORD_FUNCTION_MAX_CHARS)
            or not is_safe_model_text(reason, max_chars=RECORD_REASON_MAX_CHARS)
        ):
            return None
        keys.append(key)
        parsed.append(RecordShotMark(tile_map[key], function, reason))
    if len(keys) != len(set(keys)):
        return None
    return tuple(parsed)


def _read_cull_rejects(
    value: object,
    tile_map: Mapping[int, str],
) -> tuple[CullDecision, ...] | None:
    if not isinstance(value, list):
        return None
    parsed: list[CullDecision] = []
    keys: list[int] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"tile", "defect", "evidence"}:
            return None
        key = item.get("tile")
        defect = item.get("defect")
        evidence = item.get("evidence")
        if (
            not _is_integer_alias(key)
            or key not in tile_map
            or not isinstance(defect, str)
            or defect not in ALLOWED_CULL_DEFECTS
            or not isinstance(evidence, str)
            or evidence not in CULL_EVIDENCE_REASONS[defect]
        ):
            return None
        keys.append(key)
        parsed.append(CullDecision(tile_map[key], defect, evidence))
    if len(keys) != len(set(keys)):
        return None
    return tuple(parsed)


def _is_integer_alias(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
