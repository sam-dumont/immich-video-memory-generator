"""Drops photos that a video from the same moment already shows.

Stills shot around a video of the same scene put that instant on screen twice —
once as motion, once as a Ken Burns pan. This groups candidates by capture time
and removes the stills a motion clip already covers.
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash, hamming_distance
from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.config_models_render import PhotoConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MomentAsset:
    """A selection candidate reduced to what moment grouping needs."""

    asset_id: str
    taken_at: datetime
    thumbnail_hash: str | None = None
    covered_asset_ids: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class SuppressionResult:
    """Surviving photo IDs plus why the rest were dropped."""

    kept_ids: list[str]
    identity_drops: int = 0
    similarity_drops: int = 0
    compared: int = 0


def suppress_photos_covered_by_motion(
    photos: list[MomentAsset],
    motion: list[MomentAsset],
    *,
    gap_seconds: float,
    hash_threshold: int,
    resolve_hash: Callable[[str], str | None] | None = None,
) -> SuppressionResult:
    """Remove photos a motion clip from the same moment already shows.

    A photo is dropped when a clip carries its asset ID outright, or when a clip
    within ``gap_seconds`` is within ``hash_threshold`` bits of it. A photo whose
    hash cannot be resolved can only be dropped by the first rule — visual
    redundancy is measured, never assumed.

    ``resolve_hash`` is consulted only for assets inside a clip's time window, so
    the window bounds thumbnail I/O as well as the comparison itself.
    """
    represented = {m.asset_id for m in motion}
    represented.update(covered for m in motion for covered in m.covered_asset_ids)

    by_time = sorted(motion, key=lambda m: m.taken_at)
    times = [m.taken_at.timestamp() for m in by_time]
    hashes = _HashResolver(resolve_hash)

    kept: list[str] = []
    identity_drops = 0
    similarity_drops = 0
    compared = 0
    for candidate in photos:
        if candidate.asset_id in represented:
            identity_drops += 1
            continue

        neighbours = _clips_within_window(by_time, times, candidate, gap_seconds)
        if not neighbours:
            kept.append(candidate.asset_id)
            continue

        compared += 1
        if _matches_any(candidate, neighbours, hashes, hash_threshold):
            similarity_drops += 1
        else:
            kept.append(candidate.asset_id)

    return SuppressionResult(
        kept_ids=kept,
        identity_drops=identity_drops,
        similarity_drops=similarity_drops,
        compared=compared,
    )


class _HashResolver:
    """Memoizing view over pre-known hashes and an optional lookup callback."""

    def __init__(self, lookup: Callable[[str], str | None] | None) -> None:
        self._lookup = lookup
        self._seen: dict[str, str | None] = {}

    def of(self, asset: MomentAsset) -> str | None:
        if asset.thumbnail_hash:
            return asset.thumbnail_hash
        if self._lookup is None:
            return None
        if asset.asset_id not in self._seen:
            self._seen[asset.asset_id] = self._lookup(asset.asset_id)
        return self._seen[asset.asset_id]


def _clips_within_window(
    by_time: list[MomentAsset],
    times: list[float],
    candidate: MomentAsset,
    gap_seconds: float,
) -> list[MomentAsset]:
    at = candidate.taken_at.timestamp()
    lo = bisect.bisect_left(times, at - gap_seconds)
    hi = bisect.bisect_right(times, at + gap_seconds)
    return by_time[lo:hi]


def _matches_any(
    candidate: MomentAsset,
    neighbours: list[MomentAsset],
    hashes: _HashResolver,
    hash_threshold: int,
) -> bool:
    candidate_hash = hashes.of(candidate)
    if not candidate_hash:
        return False
    return any(
        (clip_hash := hashes.of(clip))
        and hamming_distance(candidate_hash, clip_hash) <= hash_threshold
        for clip in neighbours
    )


def filter_photos_covered_by_motion(
    photo_assets: list[Asset],
    motion_clips: list[VideoClipInfo],
    *,
    config: PhotoConfig,
    thumbnail_cache: Any = None,
    thumbnail_fn: Any = None,
) -> list[Asset]:
    """Drop photos that a video or live-photo clip from the same moment covers.

    Runs before scoring, so every photo removed here is also an LLM call saved.
    """
    if not photo_assets or not motion_clips:
        return photo_assets

    photos = [MomentAsset(asset_id=a.id, taken_at=a.file_created_at) for a in photo_assets]
    motion = [
        MomentAsset(
            asset_id=c.asset.id,
            taken_at=c.asset.file_created_at,
            covered_asset_ids=tuple(c.live_burst_still_ids or ()),
        )
        for c in motion_clips
    ]

    result = suppress_photos_covered_by_motion(
        photos,
        motion,
        gap_seconds=config.moment_gap_seconds,
        hash_threshold=config.moment_hash_threshold,
        resolve_hash=_thumbnail_hash_resolver(thumbnail_cache, thumbnail_fn),
    )

    dropped = result.identity_drops + result.similarity_drops
    if dropped:
        logger.info(
            "Moment grouping: %d photos dropped (%d already shown as motion, "
            "%d visually matched a nearby clip out of %d compared)",
            dropped,
            result.identity_drops,
            result.similarity_drops,
            result.compared,
        )

    kept = set(result.kept_ids)
    return [a for a in photo_assets if a.id in kept]


def _thumbnail_hash_resolver(
    thumbnail_cache: Any, thumbnail_fn: Any
) -> Callable[[str], str | None]:
    def resolve(asset_id: str) -> str | None:
        data = thumbnail_cache.get(asset_id, "preview") if thumbnail_cache is not None else None
        if data is None and thumbnail_fn is not None:
            try:
                data = thumbnail_fn(asset_id, size="preview")
            except (ImmichAPIError, OSError, RuntimeError, ValueError):
                return None
            if data and thumbnail_cache is not None:
                thumbnail_cache.put(asset_id, "preview", data)
        if not data:
            return None
        # WHY: thumbnails come off the wire and off disk — a truncated or
        # non-JPEG body must cost this photo its comparison, not the run.
        try:
            return compute_thumbnail_hash(data) or None
        except (OSError, TypeError, ValueError):
            return None

    return resolve
