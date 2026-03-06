"""Duplicate detection using perceptual hashing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from immich_memories.api.models import VideoClipInfo
from immich_memories.config import get_config

if TYPE_CHECKING:
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

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
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    def _compute_quality_score(self, video: VideoClipInfo) -> float:
        """Compute a quality score for ranking.

        Higher is better.
        """
        score = 0.0

        # Resolution score (40% weight)
        # Normalize to 4K as max
        if video.width and video.height:
            resolution = video.width * video.height
            max_resolution = 3840 * 2160
            score += 0.4 * min(resolution / max_resolution, 1.0)

        # Bitrate score (30% weight)
        if video.bitrate:
            # Normalize to 50 Mbps as good quality
            score += 0.3 * min(video.bitrate / 50_000_000, 1.0)

        # Duration score (20% weight)
        # Prefer longer videos (same scene, more content)
        if video.duration_seconds:
            # Normalize to 60 seconds
            score += 0.2 * min(video.duration_seconds / 60, 1.0)

        # Stability score (10% weight)
        # This would be computed from frame analysis
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


def compute_video_hash(
    video_path: str | Path,
    num_frames: int = 8,
    hash_size: int = 8,
) -> str:
    """Compute a perceptual hash for a video.

    Uses average hash (aHash) on multiple frames sampled throughout the video.

    Args:
        video_path: Path to the video file.
        num_frames: Number of frames to sample.
        hash_size: Size of the hash (hash_size x hash_size bits).

    Returns:
        Hexadecimal hash string.
    """
    video_path = Path(video_path)
    cap = cv2.VideoCapture(str(video_path))

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        return ""

    # Sample frames evenly throughout the video
    frame_indices = np.linspace(0, frame_count - 1, num_frames, dtype=int)

    frame_hashes = []
    for idx in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue

        # Compute average hash for this frame
        frame_hash = _compute_frame_hash(frame, hash_size)
        frame_hashes.append(frame_hash)

    cap.release()

    if not frame_hashes:
        return ""

    # Combine frame hashes
    # Convert each hash to binary, then combine
    combined_bits = []
    for h in frame_hashes:
        # Convert hex to binary
        bin_str = bin(int(h, 16))[2:].zfill(hash_size * hash_size)
        combined_bits.extend([int(b) for b in bin_str])

    # Create final hash by taking majority vote across frames
    final_bits = []
    chunk_size = len(combined_bits) // len(frame_hashes)
    for i in range(chunk_size):
        votes = [combined_bits[j * chunk_size + i] for j in range(len(frame_hashes))]
        final_bits.append(1 if sum(votes) > len(votes) / 2 else 0)

    # Convert to hex
    final_hash = hex(int("".join(map(str, final_bits)), 2))[2:].zfill(hash_size * hash_size // 4)
    return final_hash


def _compute_frame_hash(frame: np.ndarray, hash_size: int = 8) -> str:
    """Compute average hash for a single frame.

    Args:
        frame: BGR image as numpy array.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    # Convert to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Resize to hash_size x hash_size
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)

    # Compute mean
    mean = resized.mean()

    # Create binary hash (convert numpy bools to Python ints)
    bits = (resized > mean).flatten()

    # Convert to hex string - ensure we use Python int to avoid numpy issues
    hash_int = 0
    for i, bit in enumerate(bits):
        if bit:
            hash_int |= 1 << i

    # Use format() instead of hex() to avoid '0x' prefix issues
    return format(hash_int, "x").zfill(hash_size * hash_size // 4)


def compute_image_hash(image_path: str | Path, hash_size: int = 8) -> str:
    """Compute perceptual hash for an image.

    Args:
        image_path: Path to the image file.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return ""
    return _compute_frame_hash(img, hash_size)


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate Hamming distance between two hashes.

    Args:
        hash1: First hash (hex string).
        hash2: Second hash (hex string).

    Returns:
        Hamming distance (number of different bits).
    """
    if len(hash1) != len(hash2):
        return 64  # Maximum distance

    # Convert hex to integers
    try:
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
    except ValueError:
        return 64

    # XOR and count bits
    xor = int1 ^ int2
    return bin(xor).count("1")


def find_duplicate_groups(
    videos: list[VideoClipInfo],
    threshold: int | None = None,
    video_paths: dict[str, Path] | None = None,
) -> list[DuplicateGroup]:
    """Find groups of duplicate/similar videos.

    Args:
        videos: List of video clip info objects.
        threshold: Hamming distance threshold for duplicates.
        video_paths: Mapping of asset IDs to local video paths.

    Returns:
        List of duplicate groups.
    """
    config = get_config()
    threshold = threshold or config.analysis.duplicate_hash_threshold

    if not video_paths:
        # Cannot compute hashes without local files
        # Return each video in its own group
        return [DuplicateGroup(videos=[v]) for v in videos]

    # Compute hashes for all videos
    hashes: dict[str, str] = {}
    for video in videos:
        if video.asset.id in video_paths:
            path = video_paths[video.asset.id]
            if path.exists():
                hash_value = compute_video_hash(path)
                if hash_value:
                    hashes[video.asset.id] = hash_value

    # Build adjacency based on hash similarity
    similar_pairs: list[tuple[str, str]] = []
    video_ids = list(hashes.keys())

    for i, id1 in enumerate(video_ids):
        for id2 in video_ids[i + 1 :]:
            distance = hamming_distance(hashes[id1], hashes[id2])
            if distance <= threshold:
                similar_pairs.append((id1, id2))

    # Build groups using union-find
    groups = _union_find_groups(video_ids, similar_pairs)

    # Create video lookup
    video_lookup = {v.asset.id: v for v in videos}

    # Convert to DuplicateGroup objects
    result = []
    for group_ids in groups:
        group_videos = [video_lookup[vid] for vid in group_ids if vid in video_lookup]
        if group_videos:
            result.append(DuplicateGroup(videos=group_videos))

    # Add ungrouped videos (those without hashes)
    grouped_ids = {vid for group in groups for vid in group}
    for video in videos:
        if video.asset.id not in grouped_ids:
            result.append(DuplicateGroup(videos=[video]))

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
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if frame_count < 10:
        cap.release()
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
        bitrate = video.bitrate or 0
        duration = video.duration_seconds or 0

        return (resolution, bitrate, duration)

    return sorted(videos, key=quality_key, reverse=True)


def compute_thumbnail_hash(thumbnail_bytes: bytes, hash_size: int = 8) -> str:
    """Compute perceptual hash from thumbnail bytes.

    This is much faster than video hashing since thumbnails are already
    downloaded and cached.

    Args:
        thumbnail_bytes: JPEG image bytes from thumbnail cache.
        hash_size: Size of the hash.

    Returns:
        Hexadecimal hash string.
    """
    # Decode JPEG bytes to numpy array
    img_array = np.frombuffer(thumbnail_bytes, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if img is None:
        return ""

    return _compute_frame_hash(img, hash_size)


@dataclass
class ThumbnailCluster:
    """A cluster of similar clips based on thumbnails."""

    clip_ids: list[str]
    representative_id: str  # Best clip to represent this cluster


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
    config = get_config()
    threshold = threshold or config.analysis.duplicate_hash_threshold

    # Compute hashes for all thumbnails
    hashes: dict[str, str] = {}
    total = len(clips)

    for i, clip in enumerate(clips):
        if progress_callback:
            progress_callback(i + 1, total)

        # Get thumbnail from cache
        thumbnail_bytes = thumbnail_cache.get(clip.asset.id, "preview")
        if thumbnail_bytes is None:
            continue

        hash_value = compute_thumbnail_hash(thumbnail_bytes)
        if hash_value:
            hashes[clip.asset.id] = hash_value

    # Build adjacency based on hash similarity AND time proximity
    similar_pairs: list[tuple[str, str]] = []
    clip_ids = list(hashes.keys())

    # Create time lookup for time-based clustering
    clip_times = {c.asset.id: c.asset.file_created_at for c in clips}

    for i, id1 in enumerate(clip_ids):
        for id2 in clip_ids[i + 1 :]:
            distance = hamming_distance(hashes[id1], hashes[id2])

            # Check time proximity (clips within 2 minutes are likely related)
            time1 = clip_times.get(id1)
            time2 = clip_times.get(id2)
            time_close = False
            if time1 and time2:
                time_diff = abs((time1 - time2).total_seconds())
                time_close = time_diff < 120  # Within 2 minutes

            # Cluster if:
            # 1. Visually very similar (distance <= threshold), OR
            # 2. Time-close AND somewhat similar (distance <= threshold * 1.5)
            if distance <= threshold:
                similar_pairs.append((id1, id2))
            elif time_close and distance <= threshold * 1.5:
                similar_pairs.append((id1, id2))
                logger.debug(f"Time-based cluster: {id1[:8]} + {id2[:8]} (dist={distance}, time_diff={time_diff:.0f}s)")

    # Build groups using union-find
    groups = _union_find_groups(clip_ids, similar_pairs)

    # Create clip lookup
    clip_lookup = {c.asset.id: c for c in clips}

    # Convert to ThumbnailCluster objects
    result = []
    for group_ids in groups:
        group_clips = [clip_lookup[cid] for cid in group_ids if cid in clip_lookup]
        if group_clips:
            # Select best representative (prefer favorites, then quality)
            representative = _select_cluster_representative(group_clips)
            result.append(
                ThumbnailCluster(
                    clip_ids=group_ids,
                    representative_id=representative.asset.id,
                )
            )

    # Add ungrouped clips (those without hashes)
    grouped_ids = {cid for group in groups for cid in group}
    for clip in clips:
        if clip.asset.id not in grouped_ids:
            result.append(
                ThumbnailCluster(
                    clip_ids=[clip.asset.id],
                    representative_id=clip.asset.id,
                )
            )

    logger.info(
        f"Clustered {len(clips)} clips into {len(result)} groups "
        f"({len(clips) - len(result)} duplicates removed)"
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
        bitrate = clip.bitrate or 0
        duration = clip.duration_seconds or 0

        return (is_favorite, resolution, bitrate, duration)

    # Sort by score descending
    sorted_clips = sorted(clips, key=score_clip, reverse=True)
    return sorted_clips[0]


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
    result = []
    for cluster in clusters:
        if cluster.representative_id in clip_lookup:
            result.append(clip_lookup[cluster.representative_id])

    return result
