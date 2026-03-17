"""Duplicate detection using perceptual hashing.

Core data models, grouping logic, and video quality analysis.
Hashing functions are in duplicate_hashing.py; thumbnail clustering
is in thumbnail_clustering.py.
"""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path

import cv2
import numpy as np

from immich_memories.api.models import VideoClipInfo

from .duplicate_hashing import compute_video_hash, hamming_distance

logger = logging.getLogger(__name__)


@dataclass
class VideoHash:
    """Perceptual hash for a video."""

    asset_id: str
    hash_value: str
    hash_frames: list[str] = field(default_factory=list)
    computed_at: float = 0

    def hamming_distance(self, other: VideoHash) -> int:
        """Calculate Hamming distance to another hash."""
        if len(self.hash_value) != len(other.hash_value):
            return 64  # Maximum distance for incompatible hashes

        return sum(c1 != c2 for c1, c2 in zip(self.hash_value, other.hash_value, strict=True))


@dataclass
class DuplicateGroup:
    """A group of duplicate/similar videos."""

    videos: list[VideoClipInfo]
    similarity_scores: dict[str, float] = field(default_factory=dict)
    best_video_id: str | None = None

    def __post_init__(self):
        """Select the best video in the group."""
        if self.videos and not self.best_video_id:
            self.best_video_id = self._select_best()

    def _select_best(self) -> str:
        """Select the best video based on quality criteria."""
        if not self.videos:
            return ""

        # Score each video
        scored = []
        for video in self.videos:
            score = self._compute_quality_score(video)
            scored.append((video.asset.id, score))

        # Sort by score (descending)
        scored.sort(key=operator.itemgetter(1), reverse=True)
        return scored[0][0]

    def _compute_quality_score(self, video: VideoClipInfo) -> float:
        """Compute a quality score for ranking.

        Higher is better.
        """
        score = 0.0

        # Resolution score (40% weight)
        if video.width and video.height:
            resolution = video.width * video.height
            max_resolution = 3840 * 2160
            score += 0.4 * min(resolution / max_resolution, 1.0)

        # Bitrate score (30% weight)
        if video.bitrate:
            score += 0.3 * min(video.bitrate / 50_000_000, 1.0)

        # Duration score (20% weight)
        if video.duration_seconds:
            score += 0.2 * min(video.duration_seconds / 60, 1.0)

        # Stability score (10% weight)
        stability = self.similarity_scores.get(f"{video.asset.id}_stability", 0.5)
        score += 0.1 * stability

        return score

    @property
    def best_video(self) -> VideoClipInfo | None:
        """Get the best video in the group."""
        if not self.best_video_id:
            return self.videos[0] if self.videos else None
        for video in self.videos:
            if video.asset.id == self.best_video_id:
                return video
        return self.videos[0] if self.videos else None

    @property
    def other_videos(self) -> list[VideoClipInfo]:
        """Get all videos except the best one."""
        return [v for v in self.videos if v.asset.id != self.best_video_id]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "video_ids": [v.asset.id for v in self.videos],
            "best_video_id": self.best_video_id,
            "similarity_scores": self.similarity_scores,
        }


def _compute_video_hashes(
    videos: list[VideoClipInfo], video_paths: dict[str, Path]
) -> dict[str, str]:
    """Compute perceptual hashes for videos that have local paths."""
    hashes: dict[str, str] = {}
    for video in videos:
        path = video_paths.get(video.asset.id)
        if path and path.exists():
            hash_value = compute_video_hash(path)
            if hash_value:
                hashes[video.asset.id] = hash_value
    return hashes


def _find_similar_pairs(hashes: dict[str, str], threshold: int) -> list[tuple[str, str]]:
    """Return all (id1, id2) pairs whose hashes are within the Hamming threshold."""
    video_ids = list(hashes.keys())
    return [
        (id1, id2)
        for i, id1 in enumerate(video_ids)
        for id2 in video_ids[i + 1 :]
        if hamming_distance(hashes[id1], hashes[id2]) <= threshold
    ]


def find_duplicate_groups(
    videos: list[VideoClipInfo],
    threshold: int | None = None,
    video_paths: dict[str, Path] | None = None,
    duplicate_hash_threshold: int | None = None,
) -> list[DuplicateGroup]:
    """Find groups of duplicate/similar videos.

    Uses pairwise hash comparison with O(n^2) complexity. This is acceptable
    for typical usage (< 500 videos per run). For larger datasets, consider
    locality-sensitive hashing (LSH) for O(n log n) performance.

    Args:
        videos: List of video clip info objects.
        threshold: Hamming distance threshold for duplicates.
        video_paths: Mapping of asset IDs to local video paths.
        duplicate_hash_threshold: Default threshold from config. Falls back to get_config().

    Returns:
        List of duplicate groups.
    """
    if threshold is None:
        if duplicate_hash_threshold is not None:
            threshold = duplicate_hash_threshold
        else:
            from immich_memories.config import get_config

            threshold = get_config().analysis.duplicate_hash_threshold

    if not video_paths:
        return [DuplicateGroup(videos=[v]) for v in videos]

    hashes = _compute_video_hashes(videos, video_paths)
    similar_pairs = _find_similar_pairs(hashes, threshold)
    groups = _union_find_groups(list(hashes.keys()), similar_pairs)

    video_lookup = {v.asset.id: v for v in videos}
    result = [
        DuplicateGroup(videos=[video_lookup[vid] for vid in group_ids if vid in video_lookup])
        for group_ids in groups
        if any(vid in video_lookup for vid in group_ids)
    ]

    grouped_ids = set(chain.from_iterable(groups))
    result.extend(
        DuplicateGroup(videos=[video]) for video in videos if video.asset.id not in grouped_ids
    )

    return result


def _union_find_groups(items: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Group items using union-find based on pairs.

    Args:
        items: List of item IDs.
        pairs: List of (id1, id2) pairs that should be grouped together.

    Returns:
        List of groups, where each group is a list of IDs.
    """
    parent: dict[str, str] = {item: item for item in items}

    def find(x: str) -> str:
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Union paired items
    for id1, id2 in pairs:
        union(id1, id2)

    # Group by root
    groups: dict[str, list[str]] = {}
    for item in items:
        root = find(item)
        if root not in groups:
            groups[root] = []
        groups[root].append(item)

    return list(groups.values())


def compute_stability_score(video_path: str | Path, sample_count: int = 30) -> float:
    """Compute a stability score for a video (inverse of shake).

    Uses frame difference variance to detect camera shake.
    Lower variance = more stable = higher score.

    Args:
        video_path: Path to the video file.
        sample_count: Number of frame pairs to sample.

    Returns:
        Stability score between 0 and 1.
    """
    cap = cv2.VideoCapture(str(video_path))
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if frame_count < 10:
            return 0.5  # Default for very short videos

        # Sample frame indices
        indices = np.linspace(10, frame_count - 10, sample_count, dtype=int)

        diffs = []
        prev_frame = None

        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)

            if prev_frame is not None:
                diff = cv2.absdiff(prev_frame, gray)
                diffs.append(np.mean(diff))

            prev_frame = gray
    finally:
        cap.release()

    if not diffs:
        return 0.5

    # Calculate variance of differences
    variance = np.var(diffs)

    # Convert variance to stability score
    # Higher variance = more shake = lower stability
    # Normalize using empirical values (0-50 typical variance range)
    stability = max(0, 1 - (variance / 50))
    return float(stability)


def rank_videos_by_quality(videos: list[VideoClipInfo]) -> list[VideoClipInfo]:
    """Rank videos by quality (best first).

    Args:
        videos: List of video clip info objects.

    Returns:
        Sorted list with best quality first.
    """

    def quality_key(video: VideoClipInfo) -> tuple:
        """Generate sort key for quality ranking."""
        resolution = video.width * video.height if video.width and video.height else 0
        bitrate = video.bitrate
        duration = video.duration_seconds or 0

        return (resolution, bitrate, duration)

    return sorted(videos, key=quality_key, reverse=True)
