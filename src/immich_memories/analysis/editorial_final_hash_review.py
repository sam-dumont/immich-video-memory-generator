"""The final look-alike review a CPU-only install can run, on previews it already hashed.

The production review nominates a pair from what its pictures were described as holding and
confirms it against conserved pixels, which needs a model. Without one the review reported
`unavailable` and a no-model film ended with no review at all, while the model film ended with
one. The perceptual hashes the burst pass already caches answer the same question over the
frames a cut actually holds, keeping the picture the product would keep.

Unlike the selection-time check, this reads every frame of one story or one day against every
other, not a picture's neighbours: by the time the film is settled, two frames of the same
thing may sit four shots apart, and nothing else will ask about them again. It stops at the
edge of a story and a day, because nothing confirms a pair here and an average hash that
agrees about two occasions months apart is agreeing about how flat they are.

A refused frame does not simply leave a hole. The film asks the moment it came from for
another picture, then the story for a moment it has not shown, and only takes the shortfall
when neither has one that is not itself a repeat.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.editorial_shareability import STORY_CONTEXT_KEYS
from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS

POLICY = "final-cached-hash-duplicates-v2"

# WHY this is not `SELECTS_MAX_CORROBORATION`: that cap says how far apart two previews may be
# and still have a reader's "same" answer trusted without asking the pair the other way round.
# Nothing confirms a pair here, so the hash is the whole verdict and has to carry it alone.
# Measured at the reader's cap of 10 over thirty no-model runs: 50 of 61 refusals sat at 9 or
# 10 bits, and 20 of those were between frames months apart, the widest 246 days. At 6 of the
# 64 bits of an average hash the pass still takes what a viewer would call the same picture and
# stops guessing at the edge.
STANDALONE_REPEAT_DISTANCE = 6


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


def _within_reach(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Whether two frames are near enough in the film for one to be a repeat of the other.

    One story or one calendar day. Two frames that share neither are two occasions, and an
    average hash agreeing about them says more about how flat they are than about what they
    show.
    """
    same_story = bool(left.get("story_episode")) and left.get("story_episode") == right.get(
        "story_episode"
    )
    return same_story or str(left.get("taken", ""))[:10] == str(right.get("taken", ""))[:10]


def _first_repeat(
    carrier: Mapping[str, Any],
    kept: Sequence[Mapping[str, Any]],
    hashes: Mapping[str, str],
    distance: int,
) -> tuple[str | None, int]:
    """The frame already kept that this one repeats, and how many pairs that cost."""
    compared = 0
    asset_id = carrier["asset_id"]
    for other in kept:
        if other["asset_id"] not in hashes or not _within_reach(carrier, other):
            continue
        compared += 1
        if hamming_distance(hashes[asset_id], hashes[other["asset_id"]]) <= distance:
            return other["asset_id"], compared
    return None, compared


def _replacement_row(carrier: Mapping[str, Any], unit: Mapping[str, Any]) -> dict[str, Any]:
    """The refused carrier's place in the film, filled by the picture that takes it over.

    The story context is the slot's and stays; everything describing a picture is the
    replacement's own, so no row names a frame the film no longer shows.
    """
    return (
        {key: carrier[key] for key in STORY_CONTEXT_KEYS if key in carrier}
        | dict(unit)
        | {"why": unit.get("why") or "Replaces a frame the film already showed"}
    )


def _refill(
    carrier: Mapping[str, Any],
    offers: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    kept: Sequence[Mapping[str, Any]],
    taken: set[str],
    hashes: Mapping[str, str],
    thumbnail_hash: Callable[[str], str | None],
    distance: int,
) -> tuple[str | None, dict[str, Any] | None, int]:
    """The first offered picture that the film can show instead, and the rung it came from.

    A candidate the film already holds, one whose preview was never cached, and one that
    repeats a frame already kept are all passed over: refilling a repeat with a repeat would
    only move the problem to the next pass.
    """
    compared = 0
    for rung, unit in offers:
        asset_id = str(unit.get("asset_id") or "")
        if not asset_id or asset_id in taken:
            continue
        digest = hashes.get(asset_id) or thumbnail_hash(asset_id)
        if not digest:
            continue
        candidate = _replacement_row(carrier, unit)
        repeated, pairs = _first_repeat(candidate, kept, {**hashes, asset_id: digest}, distance)
        compared += pairs
        if repeated is None:
            return rung, candidate, compared
    return None, None, compared


def review_cut_by_cached_hashes(
    carriers: Sequence[dict[str, Any]],
    *,
    thumbnail_hash: Callable[[str], str | None],
    protected_asset_ids: Sequence[str] = (),
    distance: int = STANDALONE_REPEAT_DISTANCE,
    replacements_for: Callable[[Mapping[str, Any]], Sequence[tuple[str, Mapping[str, Any]]]]
    | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop the frames of a finished cut that repeat one it already holds, and refill the slot.

    ``replacements_for`` offers, for a refused carrier, ``(rung, unit)`` pairs in the order the
    film would rather show them. A frame with no cached hash is never removed and is named in
    the record, so a run that could not read a preview reports itself incomplete instead of
    silently keeping a repeat.
    """
    protected = frozenset(protected_asset_ids)
    hashes, unavailable = _cached_hashes(carriers, thumbnail_hash)
    taken = {c["asset_id"] for c in carriers}
    kept: list[Mapping[str, Any]] = []
    added: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    rungs: Counter[str] = Counter()
    compared = 0
    for carrier in sorted(carriers, key=lambda c: _keep_first(c, protected)):
        asset_id = carrier["asset_id"]
        skip = asset_id not in hashes or asset_id in protected
        keeper, pairs = (None, 0) if skip else _first_repeat(carrier, kept, hashes, distance)
        compared += pairs
        if keeper is None:
            kept.append(carrier)
            continue
        removal = {"asset_id": asset_id, "keeper": keeper}
        offers = replacements_for(carrier) if replacements_for else ()
        rung, replacement, pairs = _refill(
            carrier,
            offers,
            kept=kept,
            taken=taken,
            hashes=hashes,
            thumbnail_hash=thumbnail_hash,
            distance=distance,
        )
        compared += pairs
        if replacement is not None and rung is not None:
            removal["replacement"] = replacement["asset_id"]
            rungs[rung] += 1
            taken.add(replacement["asset_id"])
            hashes[replacement["asset_id"]] = thumbnail_hash(replacement["asset_id"]) or ""
            kept.append(replacement)
            added.append(replacement)
        removals.append(removal)
    removed = {row["asset_id"] for row in removals}
    survivors = [c for c in carriers if c["asset_id"] not in removed]
    if added:
        survivors = sorted([*survivors, *added], key=lambda c: str(c.get("taken", "")))
    return survivors, {
        "policy": POLICY,
        "scope": "cached preview hashes within one story or one day; no model comparison",
        "maximum_hash_distance": distance,
        "pairs_compared": compared,
        "input_carriers": len(carriers),
        "output_carriers": len(survivors),
        "removals": removals,
        "replaced_from": dict(rungs),
        "unavailable": sorted(unavailable),
        "incomplete": bool(unavailable),
    }
