"""Thumbnail-based clip clustering and deduplication.

Clusters similar clips using perceptual hashing on cached thumbnails,
with time-proximity boosting for related clips.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

from immich_memories.api.models import VideoClipInfo
from immich_memories.config import get_config

from .duplicate_hashing import compute_thumbnail_hash, hamming_distance

if TYPE_CHECKING:
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

logger = logging.getLogger(__name__)


@dataclass
class ThumbnailCluster:
    """A cluster of similar clips based on thumbnails."""

    clip_ids: list[str]
    representative_id: str  # Best clip to represent this cluster


def _compute_thumbnail_hashes(
    clips: list[VideoClipInfo],
    thumbnail_cache: ThumbnailCache,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, str]:
    """Compute perceptual hashes for all clip thumbnails.

    Args:
        clips: List of video clip info objects.
        thumbnail_cache: Cache containing thumbnails.
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        Mapping of asset ID to hex hash string.
    """
    hashes: dict[str, str] = {}
    total = len(clips)

    for i, clip in enumerate(clips):
        if progress_callback:
            progress_callback(i + 1, total)

        thumbnail_bytes = thumbnail_cache.get(clip.asset.id, "preview")
        if thumbnail_bytes is None:
            continue

        hash_value = compute_thumbnail_hash(thumbnail_bytes)
        if hash_value:
            hashes[clip.asset.id] = hash_value

    return hashes


def _build_similarity_pairs(
    hashes: dict[str, str],
    clips: list[VideoClipInfo],
    threshold: int,
) -> list[tuple[str, str]]:
    """Build pairs of similar clips based on hash distance and time proximity.

    Args:
        hashes: Mapping of asset ID to hex hash string.
        clips: List of video clip info objects (for time lookup).
        threshold: Hamming distance threshold for visual similarity.

    Returns:
        List of (id1, id2) pairs that should be clustered together.
    """
    similar_pairs: list[tuple[str, str]] = []
    clip_ids = list(hashes.keys())
    clip_durations = {c.asset.id: c.duration_seconds or 0 for c in clips}

    for i, id1 in enumerate(clip_ids):
        dur1 = clip_durations.get(id1, 0)
        for id2 in clip_ids[i + 1 :]:
            # Duration gate: true duplicates (same video, different codec)
            # always have nearly identical duration. Skip if durations differ
            # by more than 20% — these are different videos, not duplicates.
            dur2 = clip_durations.get(id2, 0)
            max_dur = max(dur1, dur2, 0.1)
            if abs(dur1 - dur2) / max_dur > 0.2:
                continue

            distance = hamming_distance(hashes[id1], hashes[id2])
            if distance <= threshold:
                similar_pairs.append((id1, id2))

    return similar_pairs


def _groups_to_clusters(
    groups: list[list[str]],
    clips: list[VideoClipInfo],
) -> list[ThumbnailCluster]:
    """Convert union-find groups into ThumbnailCluster objects.

    Also adds ungrouped clips as single-element clusters.

    Args:
        groups: Groups of clip IDs from union-find.
        clips: Full list of video clip info objects.

    Returns:
        List of ThumbnailCluster objects.
    """
    clip_lookup = {c.asset.id: c for c in clips}
    result = []

    for group_ids in groups:
        group_clips = [clip_lookup[cid] for cid in group_ids if cid in clip_lookup]
        if group_clips:
            representative = _select_cluster_representative(group_clips)
            result.append(
                ThumbnailCluster(
                    clip_ids=group_ids,
                    representative_id=representative.asset.id,
                )
            )

    # Add ungrouped clips (those without hashes)
    grouped_ids = set(chain.from_iterable(groups))
    for clip in clips:
        if clip.asset.id not in grouped_ids:
            result.append(
                ThumbnailCluster(
                    clip_ids=[clip.asset.id],
                    representative_id=clip.asset.id,
                )
            )

    return result


def _select_cluster_representative(clips: list[VideoClipInfo]) -> VideoClipInfo:
    """Select the best representative from a cluster.

    Priority:
    1. Favorite clips
    2. Higher quality (resolution, bitrate)
    3. Longer duration

    Args:
        clips: List of clips in the cluster.

    Returns:
        Best representative clip.
    """
    if len(clips) == 1:
        return clips[0]

    def score_clip(clip: VideoClipInfo) -> tuple:
        """Score a clip for ranking."""
        is_favorite = 1 if clip.asset.is_favorite else 0
        resolution = clip.width * clip.height if clip.width and clip.height else 0
        bitrate = clip.bitrate
        duration = clip.duration_seconds or 0

        return (is_favorite, resolution, bitrate, duration)

    # Sort by score descending
    sorted_clips = sorted(clips, key=score_clip, reverse=True)
    return sorted_clips[0]


def cluster_thumbnails(
    clips: list[VideoClipInfo],
    thumbnail_cache: ThumbnailCache,
    threshold: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ThumbnailCluster]:
    """Cluster clips by thumbnail similarity.

    Uses perceptual hashing on cached thumbnails for fast duplicate detection.
    Within each cluster, selects the best representative (preferring favorites).

    Args:
        clips: List of video clip info objects.
        thumbnail_cache: Cache containing thumbnails.
        threshold: Hamming distance threshold for similarity.
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        List of thumbnail clusters.
    """
    from .duplicates import _union_find_groups

    config = get_config()
    threshold = threshold or config.analysis.duplicate_hash_threshold

    hashes = _compute_thumbnail_hashes(clips, thumbnail_cache, progress_callback)

    similar_pairs = _build_similarity_pairs(hashes, clips, threshold)

    clip_ids = list(hashes.keys())
    groups = _union_find_groups(clip_ids, similar_pairs)

    result = _groups_to_clusters(groups, clips)

    logger.info(
        f"Clustered {len(clips)} clips into {len(result)} groups "
        f"({len(clips) - len(result)} duplicates removed)"
    )

    return result


def deduplicate_by_thumbnails(
    clips: list[VideoClipInfo],
    thumbnail_cache: ThumbnailCache,
    threshold: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[VideoClipInfo]:
    """Remove duplicate clips based on thumbnail similarity.

    Returns a deduplicated list where each cluster is represented by its
    best clip (preferring favorites).

    Args:
        clips: List of video clip info objects.
        thumbnail_cache: Cache containing thumbnails.
        threshold: Hamming distance threshold for similarity.
        progress_callback: Optional callback(current, total) for progress.

    Returns:
        Deduplicated list of clips.
    """
    clusters = cluster_thumbnails(
        clips=clips,
        thumbnail_cache=thumbnail_cache,
        threshold=threshold,
        progress_callback=progress_callback,
    )

    # Create clip lookup
    clip_lookup = {c.asset.id: c for c in clips}

    # Return representatives from each cluster
    return [
        clip_lookup[cluster.representative_id]
        for cluster in clusters
        if cluster.representative_id in clip_lookup
    ]
