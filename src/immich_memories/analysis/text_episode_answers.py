"""Read a text episode answer without repairing it.

Every reading is per page and per episode alias: a row the model invented, mislabelled or
contradicted is dropped and counted, and the page it belonged to is simply left unread.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256

from immich_memories.analysis.cull_answer import CULL_BUCKETS
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object
from immich_memories.store.episode_readings import (
    EpisodeCullDecision,
    EpisodeReadingIdentity,
    EpisodeRepresentative,
)

TEXT_EPISODE_SCHEMA_VERSION = "episode-reading-text-v1"
_WHAT_HAPPENED_MAX_CHARS = 300
_REPRESENTATIVE_REASON_MAX_CHARS = 120


@dataclass(frozen=True)
class _EpisodeRequestScope:
    identity: EpisodeReadingIdentity
    full_asset_ids: tuple[str, ...]
    page_asset_ids: tuple[str, ...]
    page_number: int
    page_count: int


@dataclass(frozen=True)
class _EpisodePageReading:
    scope: _EpisodeRequestScope
    what_happened: str
    representatives: tuple[EpisodeRepresentative, ...]
    cull_decisions: tuple[EpisodeCullDecision, ...]


@dataclass(frozen=True)
class EpisodeResponseDiagnostic:
    """Content-free evidence for one response that needed conservative handling."""

    response_sha256: str
    embedded_json_envelope: bool = False
    unreadable_or_omitted_pages: int = 0
    discarded_invalid_representative_rows: int = 0
    discarded_invalid_cull_rows: int = 0
    discarded_conflicting_cull_rows: int = 0


@dataclass(frozen=True)
class TextEpisodeReadDiagnostics:
    """Inspectable normalizations without retaining private provider prose."""

    responses: tuple[EpisodeResponseDiagnostic, ...] = ()

    @property
    def embedded_json_envelopes(self) -> int:
        return sum(response.embedded_json_envelope for response in self.responses)

    @property
    def discarded_invalid_representative_rows(self) -> int:
        return sum(response.discarded_invalid_representative_rows for response in self.responses)

    @property
    def discarded_invalid_cull_rows(self) -> int:
        return sum(response.discarded_invalid_cull_rows for response in self.responses)

    @property
    def discarded_conflicting_cull_rows(self) -> int:
        return sum(response.discarded_conflicting_cull_rows for response in self.responses)


def episode_diagnostics_record(
    diagnostics: TextEpisodeReadDiagnostics,
) -> dict[str, object]:
    """Serialize only content-free parser evidence for a private run artifact."""
    return {
        "embedded_json_envelopes": diagnostics.embedded_json_envelopes,
        "discarded_invalid_representative_rows": (
            diagnostics.discarded_invalid_representative_rows
        ),
        "discarded_invalid_cull_rows": diagnostics.discarded_invalid_cull_rows,
        "discarded_conflicting_cull_rows": diagnostics.discarded_conflicting_cull_rows,
        "responses": [asdict(response) for response in diagnostics.responses],
    }


@dataclass(frozen=True)
class _EpisodeResponse:
    readings: tuple[_EpisodePageReading, ...]
    diagnostic: EpisodeResponseDiagnostic | None


@dataclass(frozen=True)
class _EpisodePageParse:
    reading: _EpisodePageReading | None
    discarded_invalid_representative_rows: int = 0
    discarded_invalid_cull_rows: int = 0
    discarded_conflicting_cull_rows: int = 0


def _unreadable(response_hash: str, scopes, *, embedded: bool = False) -> _EpisodeResponse:
    return _EpisodeResponse(
        (),
        EpisodeResponseDiagnostic(
            response_sha256=response_hash,
            embedded_json_envelope=embedded,
            unreadable_or_omitted_pages=len(scopes),
        ),
    )


def _rows_by_alias(values: list) -> dict[int, list[object]]:
    by_alias: dict[int, list[object]] = {}
    for value in values:
        if isinstance(value, dict) and _is_alias(value.get("episode")):
            by_alias.setdefault(value["episode"], []).append(value)
    return by_alias


def _response_diagnostic(response_hash, *, embedded, omitted, counts):
    invalid_representatives, invalid_cull, conflicting_cull = counts
    if not (embedded or omitted or invalid_representatives or invalid_cull or conflicting_cull):
        return None
    return EpisodeResponseDiagnostic(
        response_sha256=response_hash,
        embedded_json_envelope=embedded,
        unreadable_or_omitted_pages=omitted,
        discarded_invalid_representative_rows=invalid_representatives,
        discarded_invalid_cull_rows=invalid_cull,
        discarded_conflicting_cull_rows=conflicting_cull,
    )


def _read_response_result(
    raw: str,
    scopes: tuple[_EpisodeRequestScope, ...],
) -> _EpisodeResponse:
    """Every scope the answer read validly, plus what had to be discarded to get there."""
    payload, embedded = _episode_payload(raw)
    response_hash = sha256(raw.encode("utf-8")).hexdigest()
    if payload is None:
        return _unreadable(response_hash, scopes)
    values = payload.get("episodes")
    if not isinstance(values, list):
        return _unreadable(response_hash, scopes, embedded=embedded)
    by_alias = _rows_by_alias(values)
    readings: list[_EpisodePageReading] = []
    discarded = [0, 0, 0]
    for episode_alias, scope in enumerate(scopes, start=1):
        matches = by_alias.get(episode_alias, [])
        if len(matches) != 1 or not isinstance(matches[0], dict):
            continue
        parsed = _one_reading(matches[0], scope)
        discarded[0] += parsed.discarded_invalid_representative_rows
        discarded[1] += parsed.discarded_invalid_cull_rows
        discarded[2] += parsed.discarded_conflicting_cull_rows
        if parsed.reading is not None:
            readings.append(parsed.reading)
    diagnostic = _response_diagnostic(
        response_hash,
        embedded=embedded,
        omitted=len(scopes) - len(readings),
        counts=discarded,
    )
    return _EpisodeResponse(tuple(readings), diagnostic)


def _read_response(
    raw: str,
    scopes: tuple[_EpisodeRequestScope, ...],
) -> tuple[_EpisodePageReading, ...]:
    return _read_response_result(raw, scopes).readings


def _episode_payload(raw: str) -> tuple[dict[str, object] | None, bool]:
    payload = final_json_object(raw)
    if payload is not None and payload.get("schema_version") == TEXT_EPISODE_SCHEMA_VERSION:
        return payload, False
    decoder = json.JSONDecoder()
    candidates: list[dict[str, object]] = []
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema_version") == TEXT_EPISODE_SCHEMA_VERSION:
            candidates.append(value)
    return (candidates[0], True) if len(candidates) == 1 else (None, False)


def _one_reading(
    value: Mapping[str, object],
    scope: _EpisodeRequestScope,
) -> _EpisodePageParse:
    what_happened = bounded_model_text(
        value.get("what_happened"), max_chars=_WHAT_HAPPENED_MAX_CHARS
    )
    representatives, discarded_representatives = _representatives(
        value.get("representatives"), scope.page_asset_ids
    )
    if what_happened is None or not representatives:
        return _EpisodePageParse(
            None,
            discarded_invalid_representative_rows=discarded_representatives,
        )
    cull_decisions, discarded_invalid, discarded_conflicting = _cull_decisions(
        value.get("cull"),
        scope.page_asset_ids,
        representative_ids=frozenset(item.asset_id for item in representatives),
    )
    return _EpisodePageParse(
        _EpisodePageReading(
            scope=scope,
            what_happened=what_happened,
            representatives=representatives,
            cull_decisions=cull_decisions,
        ),
        discarded_invalid_representative_rows=discarded_representatives,
        discarded_invalid_cull_rows=discarded_invalid,
        discarded_conflicting_cull_rows=discarded_conflicting,
    )


def _representative_named(item: object, asset_ids: tuple[str, ...]) -> tuple[str, str] | None:
    """The asset and reason one representative row names, or None when it names neither."""
    if not isinstance(item, dict) or not _is_alias(item.get("asset")):
        return None
    asset_alias = item["asset"]
    reason = bounded_model_text(item.get("reason"), max_chars=_REPRESENTATIVE_REASON_MAX_CHARS)
    if reason is None or not 1 <= asset_alias <= len(asset_ids):
        return None
    return asset_ids[asset_alias - 1], reason


def _representatives(
    value: object,
    asset_ids: tuple[str, ...],
) -> tuple[tuple[EpisodeRepresentative, ...], int]:
    if not isinstance(value, list):
        return (), 1
    representatives: list[EpisodeRepresentative] = []
    seen: set[str] = set()
    discarded = 0
    for item in value:
        named = _representative_named(item, asset_ids)
        if named is None or named[0] in seen or len(representatives) == 3:
            discarded += 1
            continue
        seen.add(named[0])
        representatives.append(EpisodeRepresentative(*named))
    return tuple(representatives), discarded


def _cull_named(item: object, asset_ids: tuple[str, ...]) -> tuple[str, str] | None:
    """The asset and bucket one cull row names, or None when it names neither."""
    if (
        not isinstance(item, dict)
        or not _is_alias(item.get("asset"))
        or item.get("bucket") not in CULL_BUCKETS
        or not 1 <= item["asset"] <= len(asset_ids)
    ):
        return None
    return asset_ids[item["asset"] - 1], str(item["bucket"])


def _cull_decisions(
    value: object,
    asset_ids: tuple[str, ...],
    *,
    representative_ids: frozenset[str],
) -> tuple[tuple[EpisodeCullDecision, ...], int, int]:
    if not isinstance(value, list):
        return (), 1, 0
    decisions: list[EpisodeCullDecision] = []
    seen: set[str] = set()
    discarded_invalid = 0
    discarded_conflicting = 0
    for item in value:
        named = _cull_named(item, asset_ids)
        if named is None:
            discarded_invalid += 1
        elif named[0] in representative_ids:
            discarded_conflicting += 1
        elif named[0] in seen:
            discarded_invalid += 1
        else:
            seen.add(named[0])
            decisions.append(EpisodeCullDecision(*named))
    return tuple(decisions), discarded_invalid, discarded_conflicting


def _is_alias(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
