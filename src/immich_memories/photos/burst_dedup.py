"""One photo per burst, not five.

A phone shutter held down produces near-identical frames seconds apart. Nothing
removed them from the photo pool: `deduplicate_by_thumbnails` exists but is only
ever called on video clips, and it clusters on hash distance alone, so pointing
it at photos would merge the same kitchen photographed a month apart.

Measured on a real June pool: 64 of 303 photos are near-duplicates of another
shot within five minutes -- 21% of the pool, in groups of up to five.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from immich_memories.analysis.duplicate_hashing import hamming_distance

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhotoCandidate:
    """A photo reduced to what burst detection needs."""

    key: str
    taken_at: datetime
    thumbnail_hash: str | None
    score: float = 0.0


def drop_burst_duplicates(
    photos: list[PhotoCandidate],
    *,
    window_seconds: float,
    hash_threshold: int,
) -> list[str]:
    """Keep the best-scored photo from each burst, in input order.

    A burst is photos taken within ``window_seconds`` of each other whose
    thumbnails are within ``hash_threshold`` bits. Both conditions are required:
    time alone would collapse a busy minute at a party, and similarity alone
    would merge the same room across months.

    A photo with no thumbnail hash is always kept. Redundancy is measured, never
    assumed -- a missing thumbnail says nothing about the picture.
    """
    if not photos:
        return []

    by_time = sorted((p for p in photos if p.thumbnail_hash), key=lambda p: p.taken_at)

    superseded: set[str] = set()
    for index, candidate in enumerate(by_time):
        if candidate.key in superseded:
            continue
        burst = _burst_starting_at(by_time, index, window_seconds, hash_threshold, superseded)
        if len(burst) > 1:
            best = max(burst, key=lambda p: p.score)
            superseded.update(p.key for p in burst if p.key != best.key)

    if superseded:
        logger.info(
            "Burst de-duplication: %d of %d photos dropped as near-identical frames within %.0fs",
            len(superseded),
            len(photos),
            window_seconds,
        )
    return [p.key for p in photos if p.key not in superseded]


def _burst_starting_at(
    by_time: list[PhotoCandidate],
    index: int,
    window_seconds: float,
    hash_threshold: int,
    superseded: set[str],
) -> list[PhotoCandidate]:
    """Photos from `index` onward that belong to the same burst as it."""
    first = by_time[index]
    burst = [first]
    for other in by_time[index + 1 :]:
        if (other.taken_at - first.taken_at).total_seconds() > window_seconds:
            break  # sorted by time: nothing further can be in this burst
        if other.key in superseded:
            continue
        distance = hamming_distance(first.thumbnail_hash or "", other.thumbnail_hash or "")
        if distance <= hash_threshold:
            burst.append(other)
    return burst
