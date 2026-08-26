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
# Read, validated, and thrown away. Without somewhere to put "merely
# unremarkable" the model puts it in notes: measured at temperature 0 on one
# real pack, notes held 19 tiles of which nine were fields and cycling paths.
# Given this third list it held four -- a photographed document and three shots
# of a television -- and nothing else. Choosing between similar frames is a
# later pass's work, so what lands here is not Cull's to act on.
DISCARDED_BUCKETS = ("ordinary",)
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
    read = _read_scoped_rejects(payload.get("cull_rejects"), tile_map, episode_tiles)
    if read is None:
        return ParsedCullNamespaces(cull_rejects=(), warnings=(), cull_valid=False)
    rejects, misfiled = read
    kept, warnings = _discard_unavailable_decisions(rejects, unavailable_asset_ids)
    return ParsedCullNamespaces(cull_rejects=kept, warnings=misfiled + warnings, cull_valid=True)


def _read_scoped_rejects(
    value: object,
    tile_map: Mapping[int, str],
    episode_tiles: Mapping[int, tuple[int, ...]],
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]] | None:
    if not isinstance(value, list):
        return None
    seen_episodes: set[int] = set()
    verdicts: dict[str, set[str]] = {}
    misfiled: list[str] = []
    for entry in value:
        read = _one_episode_entry(entry, tile_map, episode_tiles, seen_episodes, verdicts)
        if read is None:
            return None
        misfiled.extend(read)
    return _resolve_verdicts(verdicts, tuple(misfiled))


def _resolve_verdicts(
    verdicts: dict[str, set[str]], misfiled: tuple[str, ...]
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    """One decision per asset, resolving a disagreement toward keeping it.

    Naming the same tile twice with the same verdict agrees with itself and is
    one decision. Naming it with two different verdicts is a contradiction, and
    a contradiction about whether to remove something resolves the only safe
    way: the asset stays, and the trace says why.
    """
    decisions: list[CullDecision] = []
    warnings = list(misfiled)
    for asset_id, buckets in verdicts.items():
        if len(buckets) > 1:
            warnings.append(f"!! Cull gave contradictory verdicts for {asset_id}")
            continue
        decisions.append(CullDecision(asset_id, next(iter(buckets))))
    return tuple(decisions), tuple(warnings)


def _one_episode_entry(
    entry: object,
    tile_map: Mapping[int, str],
    episode_tiles: Mapping[int, tuple[int, ...]],
    seen_episodes: set[int],
    verdicts: dict[str, set[str]],
) -> tuple[str, ...] | None:
    """This episode's misfiling notes, or None when the answer left the sheet."""
    episode = _entry_episode(entry, episode_tiles, seen_episodes)
    if episode is None:
        return None
    scope = frozenset(episode_tiles[episode])
    misfiled: list[str] = []
    assert isinstance(entry, dict)
    for bucket in DISCARDED_BUCKETS:
        named = entry.get(bucket)
        # The partition binds the lists that REMOVE things. A discarded list
        # cannot contradict anything that matters, so it is checked only for
        # naming tiles this sheet really shows: a real month sent episode 1's
        # ordinary list naming every tile and each later episode naming its own
        # again, and holding that to the partition voided two packs of five.
        if named is not None and _tiles_in(named, tile_map) is None:
            return None
    for bucket in CULL_BUCKETS:
        tiles = _tiles_in(entry.get(bucket), tile_map)
        if tiles is None:
            return None
        # Which episode a tile was filed under is bookkeeping; the judgement is
        # about pixels this sheet really shows.
        misfiled.extend(
            f"!! Cull filed tile {tile} under episode {episode}"
            for tile in tiles
            if tile not in scope
        )
        for tile in tiles:
            verdicts.setdefault(tile_map[tile], set()).add(bucket)
    return tuple(misfiled)


def _entry_episode(
    entry: object,
    episode_tiles: Mapping[int, tuple[int, ...]],
    seen_episodes: set[int],
) -> int | None:
    """The episode this entry answers for, or None if it is not a usable entry."""
    if not isinstance(entry, dict):
        return None
    # The discarded lists are optional: an answer that omits one has still
    # answered, and voiding it over a key nothing acts on would be absurd.
    if set(entry) - set(DISCARDED_BUCKETS) != set(CULL_SCOPE_WIRE_KEYS):
        return None
    episode = entry.get("episode")
    if not _is_integer_alias(episode) or episode not in episode_tiles:
        return None
    if episode in seen_episodes:
        return None
    seen_episodes.add(episode)
    return episode


def _tiles_in(
    value: object,
    tile_map: Mapping[int, str],
) -> tuple[int, ...] | None:
    """Tiles named for one bucket, or None when the answer left the sheet."""
    if not isinstance(value, list):
        return None
    tiles: list[int] = []
    for tile in value:
        # A tile this pack never showed means the answer stopped tracking which
        # sheet it was reading, and nothing in it can be trusted.
        if not _is_integer_alias(tile) or tile not in tile_map:
            return None
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
