"""The final look-alike review a CPU-only install can run, on previews it already hashed.

The production review nominates a pair from what its pictures were described as holding and
confirms it against conserved pixels, which needs a model. Without one the review reported
`unavailable` and a no-model film ended with no review at all, while the model film ended with
one. The perceptual hashes the burst pass already caches answer the same question over the
frames a cut actually holds: every pair of the finished film, at the production corroboration
distance, keeping the picture the product would keep.

Unlike the selection-time check, this compares the whole cut rather than a picture's
neighbours: by the time the film is settled, two frames of the same thing may sit four shots
apart on a thin day, and nothing else will ask about them again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS
from immich_memories.analysis.selection_same_picture import SELECTS_MAX_CORROBORATION

POLICY = "final-cached-hash-duplicates-v1"


def _keep_first(carrier: Mapping[str, Any], protected: frozenset[str]) -> tuple:
    """Which of two look-alikes stays: the owner's tick, then the star, then motion, then the
    earlier frame. A video product never drops the clip of a thing for a still of it."""
    return (
        carrier["asset_id"] not in protected,
        not carrier.get("favourite"),
        carrier.get("kind") not in MOTION_KINDS,
        datetime.fromisoformat(carrier["taken"]),
        carrier["asset_id"],
    )


def _cached_hashes(
    carriers: Sequence[dict[str, Any]], thumbnail_hash: Callable[[str], str | None]
) -> tuple[dict[str, str], list[str]]:
    """The preview hash of every frame in the cut, and the frames that have none."""
    hashes: dict[str, str] = {}
    unavailable: list[str] = []
    for carrier in carriers:
        digest = thumbnail_hash(carrier["asset_id"])
        if digest:
            hashes[carrier["asset_id"]] = digest
        else:
            unavailable.append(carrier["asset_id"])
    return hashes, unavailable


def _first_repeat(
    asset_id: str, kept: Sequence[str], hashes: Mapping[str, str], distance: int
) -> tuple[str | None, int]:
    """The frame already kept that this one repeats, and how many pairs that cost."""
    compared = 0
    for other in kept:
        if other not in hashes:
            continue
        compared += 1
        if hamming_distance(hashes[asset_id], hashes[other]) <= distance:
            return other, compared
    return None, compared


def review_cut_by_cached_hashes(
    carriers: Sequence[dict[str, Any]],
    *,
    thumbnail_hash: Callable[[str], str | None],
    protected_asset_ids: Sequence[str] = (),
    distance: int = SELECTS_MAX_CORROBORATION,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop the frames of a finished cut that repeat one it already holds.

    A frame with no cached hash is never removed and is named in the record, so a run that
    could not read a preview reports itself incomplete instead of silently keeping a repeat.
    """
    protected = frozenset(protected_asset_ids)
    hashes, unavailable = _cached_hashes(carriers, thumbnail_hash)
    kept: list[str] = []
    removals: list[dict[str, Any]] = []
    compared = 0
    for carrier in sorted(carriers, key=lambda c: _keep_first(c, protected)):
        asset_id = carrier["asset_id"]
        skip = asset_id not in hashes or asset_id in protected
        keeper, pairs = (None, 0) if skip else _first_repeat(asset_id, kept, hashes, distance)
        compared += pairs
        if keeper is None:
            kept.append(asset_id)
        else:
            removals.append({"asset_id": asset_id, "keeper": keeper})
    removed = {row["asset_id"] for row in removals}
    survivors = [c for c in carriers if c["asset_id"] not in removed]
    return survivors, {
        "policy": POLICY,
        "scope": "cached preview hashes over the whole finished cut; no model comparison",
        "maximum_hash_distance": distance,
        "pairs_compared": compared,
        "input_carriers": len(carriers),
        "output_carriers": len(survivors),
        "removals": removals,
        "unavailable": sorted(unavailable),
        "incomplete": bool(unavailable),
    }
