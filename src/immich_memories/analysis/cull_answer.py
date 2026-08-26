"""Strict parsing for the two Cull buckets, asked inside each episode's scope.

Cull removes the junk and the failed pictures, and protects the favourites. It
never chooses between similar frames: a real month held runs of eight to
thirty-five near-duplicates, and deciding between those is a later pass's work.

The per-episode shape is not presentation. Probed on one real 57-tile pack,
three repeats each: a flat answer over the whole pack parsed once in three and
gave fifty-five of fifty-seven tiles the same label, while the same question
asked inside each episode's own scope parsed three times in three and returned
identical tiles every run. A small model cannot search a large flat set; it
answers reliably inside a named small scope.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeGuard

from immich_memories.analysis.period_insight_answer import (
    EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
    EPISODE_SCAN_SCHEMA_VERSION,
    EPISODE_VISUAL_SUMMARY_MAX_CHARS,
)
from immich_memories.analysis.strict_json import final_json_object

# Two buckets, because a vocabulary of named defects was measured collapsing:
# shown one populated example the model copied its defect onto seven unrelated
# visuals, and asked to choose among four families it produced a pair that was
# not even legal. The bucket carries the reason; the model only sorts.
CULL_BUCKET_REASONS = {
    "notes": "taken as a note rather than as a moment",
    "failed": "the picture did not come out",
}
CULL_BUCKETS = tuple(CULL_BUCKET_REASONS)
CULL_SCOPE_WIRE_KEYS = ("episode", *CULL_BUCKETS)
_RESPONSE_PLANNING_CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class CullDecision:
    """One visual Cull removed, and which of its two questions removed it."""

    asset_id: str
    bucket: str
    reason: str = field(init=False)

    def __post_init__(self) -> None:
        reason = CULL_BUCKET_REASONS.get(self.bucket)
        if not self.asset_id.strip() or reason is None:
            raise ValueError("Cull decision needs a stable asset and a known bucket")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True)
class ParsedCullNamespaces:
    """Validated Pass 1 decisions from one banked scan."""

    cull_rejects: tuple[CullDecision, ...]
    warnings: tuple[str, ...]
    cull_valid: bool


def fused_episode_response_fits(
    displayed_by_episode: tuple[tuple[int, ...], ...],
    *,
    max_output_tokens: int,
) -> bool:
    """Whether the largest valid fused response still fits its output envelope."""
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
    # Worst case is every tile named once: a tile cannot sit in both buckets.
    rejects = tuple(
        {"episode": episode_alias, "notes": list(displayed), "failed": []}
        for episode_alias, displayed in enumerate(displayed_by_episode, start=1)
    )
    envelope = json.dumps(
        {
            "schema_version": EPISODE_SCAN_SCHEMA_VERSION,
            "pack": 1,
            "episode_readings": readings,
            "cull_rejects": rejects,
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
    episode_tiles: Mapping[int, tuple[int, ...]],
    unavailable_asset_ids: frozenset[str] = frozenset(),
) -> ParsedCullNamespaces | None:
    """Read Pass 1 without repairing a malformed outer response."""
    payload = final_json_object(raw)
    if (
        payload is None
        or payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION
        or not _is_integer_alias(payload.get("pack"))
        or payload.get("pack") != pack_alias
    ):
        return None
    rejects = _read_scoped_rejects(payload.get("cull_rejects"), tile_map, episode_tiles)
    if rejects is None:
        return ParsedCullNamespaces(cull_rejects=(), warnings=(), cull_valid=False)
    kept, warnings = _discard_unavailable_decisions(rejects, unavailable_asset_ids)
    return ParsedCullNamespaces(cull_rejects=kept, warnings=warnings, cull_valid=True)


def _read_scoped_rejects(
    value: object,
    tile_map: Mapping[int, str],
    episode_tiles: Mapping[int, tuple[int, ...]],
) -> tuple[CullDecision, ...] | None:
    if not isinstance(value, list):
        return None
    seen_episodes: set[int] = set()
    seen_tiles: set[int] = set()
    parsed: list[CullDecision] = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != set(CULL_SCOPE_WIRE_KEYS):
            return None
        episode = entry.get("episode")
        if not _is_integer_alias(episode) or episode not in episode_tiles:
            return None
        if episode in seen_episodes:
            return None
        seen_episodes.add(episode)
        scope = frozenset(episode_tiles[episode])
        for bucket in CULL_BUCKETS:
            tiles = _tiles_in(entry.get(bucket), scope, tile_map, seen_tiles)
            if tiles is None:
                return None
            parsed.extend(CullDecision(tile_map[tile], bucket) for tile in tiles)
    return tuple(parsed)


def _tiles_in(
    value: object,
    scope: frozenset[int],
    tile_map: Mapping[int, str],
    seen_tiles: set[int],
) -> tuple[int, ...] | None:
    """Tiles named for one bucket, or None when the answer left its own scope."""
    if not isinstance(value, list):
        return None
    tiles: list[int] = []
    for tile in value:
        # Scope is the whole mechanism. A tile named under an episode it does
        # not belong to means the answer stopped tracking which sheet it was
        # reading, and nothing in it can be trusted.
        if not _is_integer_alias(tile) or tile not in scope or tile not in tile_map:
            return None
        if tile in seen_tiles:
            return None
        seen_tiles.add(tile)
        tiles.append(tile)
    return tuple(tiles)


def _discard_unavailable_decisions(
    rejects: tuple[CullDecision, ...],
    unavailable_asset_ids: frozenset[str],
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    """A decision about pixels nobody could see is not a decision."""
    unavailable = tuple(
        decision.asset_id for decision in rejects if decision.asset_id in unavailable_asset_ids
    )
    kept = tuple(decision for decision in rejects if decision.asset_id not in unavailable_asset_ids)
    warnings = tuple(f"!! unavailable Cull decision: {asset_id}" for asset_id in unavailable)
    return kept, warnings


def _is_integer_alias(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
