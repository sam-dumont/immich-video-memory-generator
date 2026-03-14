"""Video analysis pipeline for batch processing and clustering."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import CachedVideoAnalysis, VideoAnalysisCache
    from immich_memories.cache.video_cache import VideoDownloadCache

logger = logging.getLogger(__name__)


@dataclass
class DuplicateCluster:
    """A cluster of similar/duplicate videos."""

    cluster_id: int
    asset_ids: list[str]
    best_asset_id: str
    similarity_scores: dict[str, int] = field(default_factory=dict)  # asset_id -> hamming distance

    @property
    def size(self) -> int:
        """Get the number of videos in this cluster."""
        return len(self.asset_ids)

    def get_duplicates(self) -> list[str]:
        """Get asset IDs of duplicates (non-best videos)."""
        return [aid for aid in self.asset_ids if aid != self.best_asset_id]


class VideoAnalyzer:
    """Analyzes videos and caches results."""

    def __init__(
        self,
        client: SyncImmichClient,
        cache: VideoAnalysisCache,
        video_cache: VideoDownloadCache | None = None,
    ):
        """Initialize the analyzer.

        Args:
            client: Immich API client for downloading videos.
            cache: Cache for storing analysis results.
            video_cache: Optional video file cache to avoid re-downloads.
        """
        self.client = client
        self.cache = cache
        self.video_cache: VideoDownloadCache | None = None

        # Initialize video cache if enabled
        if video_cache is None:
            from immich_memories.config import get_config

            config = get_config()
            if config.cache.video_cache_enabled:
                from immich_memories.cache.video_cache import VideoDownloadCache

                self.video_cache = VideoDownloadCache(
                    cache_dir=config.cache.video_cache_path,
                    max_size_gb=config.cache.video_cache_max_size_gb,
                    max_age_days=config.cache.video_cache_max_age_days,
                )
            else:
                self.video_cache = None
        else:
            self.video_cache = video_cache

    def analyze_batch(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[int, int, str], None] | None = None,
        prioritize_favorites: bool = True,
        segment_duration: float = 3.0,
    ) -> list[CachedVideoAnalysis]:
        """Analyze a batch of videos, using cache where available.

        Args:
            clips: Videos to analyze.
            progress_callback: Callback with (current, total, asset_name).
            prioritize_favorites: Analyze favorites first.
            segment_duration: Duration of segments for sampling.

        Returns:
            List of cached analysis results.
        """

        # Sort: favorites first, then by date
        sorted_clips = self._prioritize_clips(clips, prioritize_favorites)

        # Get checksums for cache invalidation
        checksums = {c.asset.id: c.asset.checksum for c in clips}

        # Find what needs analysis
        uncached = set(
            self.cache.get_uncached_asset_ids(
                [c.asset.id for c in sorted_clips],
                checksums,
            )
        )

        results: list[CachedVideoAnalysis] = []
        for i, clip in enumerate(sorted_clips):
            if progress_callback:
                progress_callback(
                    i + 1,
                    len(sorted_clips),
                    clip.asset.original_file_name or clip.asset.id[:8],
                )

            if clip.asset.id in uncached:
                try:
                    analysis = self._analyze_video(clip, segment_duration)
                    if analysis:
                        results.append(analysis)
                except Exception as e:
                    logger.error(f"Failed to analyze {clip.asset.id}: {e}")
                    continue
            else:
                cached = self.cache.get_analysis(clip.asset.id)
                if cached:
                    results.append(cached)

        return results

    def _prioritize_clips(
        self,
        clips: list[VideoClipInfo],
        prioritize_favorites: bool,
    ) -> list[VideoClipInfo]:
        """Sort clips with favorites first, then by date.

        Args:
            clips: Clips to sort.
            prioritize_favorites: Whether to prioritize favorites.

        Returns:
            Sorted list of clips.
        """
        if not prioritize_favorites:
            return sorted(clips, key=lambda c: c.asset.file_created_at or datetime.min)

        favorites = []
        others = []
        for clip in clips:
            if clip.asset.is_favorite:
                favorites.append(clip)
            else:
                others.append(clip)

        # Sort each group by date
        favorites.sort(key=lambda c: c.asset.file_created_at or datetime.min)
        others.sort(key=lambda c: c.asset.file_created_at or datetime.min)

        return favorites + others

    @staticmethod
    def _get_safe_video_suffix(filename: str | None) -> str:
        """Return a safe file extension from a video filename, defaulting to .mp4."""
        _safe_exts = {
            ".mp4",
            ".mov",
            ".avi",
            ".mkv",
            ".webm",
            ".m4v",
            ".wmv",
            ".flv",
            ".mpeg",
            ".mpg",
            ".3gp",
            ".ts",
        }
        raw = Path(filename or "video.mp4").suffix or ".mp4"
        return raw if raw.isalnum() or raw in _safe_exts else ".mp4"

    def _analyze_video(
        self,
        clip: VideoClipInfo,
        segment_duration: float,
    ) -> CachedVideoAnalysis | None:
        """Download and analyze a single video.

        Args:
            clip: Video clip to analyze.
            segment_duration: Duration of segments for sampling.

        Returns:
            Cached analysis result or None if failed.
        """
        import tempfile

        from immich_memories.analysis.duplicates import compute_video_hash
        from immich_memories.analysis.scoring import SceneScorer

        video_path: Path | None = None
        temp_file: Path | None = None

        try:
            if self.video_cache:
                logger.info(f"Getting {clip.asset.id} from video cache...")
                video_path = self.video_cache.download_or_get(self.client, clip.asset)
            else:
                suffix = self._get_safe_video_suffix(clip.asset.original_file_name)
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    temp_file = Path(tmp.name)
                logger.info(f"Downloading {clip.asset.id} to temp file...")
                self.client.download_asset(clip.asset.id, temp_file)
                video_path = temp_file

            if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
                logger.error(f"Failed to get video for {clip.asset.id}")
                return None

            logger.info(f"Computing hash for {clip.asset.id}...")
            try:
                video_hash = compute_video_hash(str(video_path))
            except Exception as e:
                logger.warning(f"Failed to compute hash: {e}")
                video_hash = None

            logger.info(f"Scoring segments for {clip.asset.id}...")
            scorer = SceneScorer()
            moments = scorer.sample_and_score_video(
                video_path, segment_duration=segment_duration, overlap=0.5, sample_frames=5
            )

            self.cache.save_analysis(
                asset=clip.asset, video_info=clip, perceptual_hash=video_hash, segments=moments
            )
            return self.cache.get_analysis(clip.asset.id)

        except Exception as e:
            logger.error(f"Analysis failed for {clip.asset.id}: {e}")
            return None
        finally:
            if temp_file:
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass

    def get_analysis_status(
        self,
        clips: list[VideoClipInfo],
    ) -> tuple[int, int]:
        """Get analysis status for a batch of clips.

        Args:
            clips: Clips to check.

        Returns:
            Tuple of (cached_count, uncached_count).
        """
        checksums = {c.asset.id: c.asset.checksum for c in clips}
        uncached = self.cache.get_uncached_asset_ids(
            [c.asset.id for c in clips],
            checksums,
        )
        return len(clips) - len(uncached), len(uncached)


class ClusterManager:
    """Manages duplicate clustering based on perceptual hashes."""

    def __init__(self, cache: VideoAnalysisCache):
        """Initialize the cluster manager.

        Args:
            cache: Cache containing video hashes.
        """
        self.cache = cache

    def _build_cluster_from_group(
        self,
        cluster_id: int,
        group_ids: set[str],
        clip_lookup: dict[str, VideoClipInfo],
        hashes: dict[str, str],
    ) -> DuplicateCluster | None:
        """Build a DuplicateCluster from a group of IDs, or None for singletons."""
        if len(group_ids) <= 1:
            return None
        best_id = max(
            group_ids,
            key=lambda aid: (
                (clip_lookup[aid].width or 0) * (clip_lookup[aid].height or 0)
                if aid in clip_lookup
                else 0
            ),
        )
        best_hash = hashes.get(best_id, "")
        similarity_scores = {
            aid: self._hamming_distance(best_hash, hashes.get(aid, ""))
            for aid in group_ids
            if aid != best_id
        }
        return DuplicateCluster(
            cluster_id=cluster_id,
            asset_ids=list(group_ids),
            best_asset_id=best_id,
            similarity_scores=similarity_scores,
        )

    def build_clusters(
        self,
        clips: list[VideoClipInfo],
        threshold: int = 8,
    ) -> list[DuplicateCluster]:
        """Build clusters from perceptual hashes.

        Args:
            clips: Videos to cluster.
            threshold: Max Hamming distance to consider similar.

        Returns:
            List of duplicate clusters (only multi-video clusters).
        """
        hashes = self.cache.get_all_hashes()
        clip_ids = {c.asset.id for c in clips}
        hashes = {k: v for k, v in hashes.items() if k in clip_ids}

        if len(hashes) < 2:
            return []

        asset_ids = list(hashes.keys())
        similar_pairs: list[tuple[str, str, int]] = [
            (id1, id2, dist)
            for i, id1 in enumerate(asset_ids)
            for id2 in asset_ids[i + 1 :]
            if (dist := self._hamming_distance(hashes[id1], hashes[id2])) <= threshold
        ]

        groups = self._union_find(asset_ids, similar_pairs)
        clip_lookup = {c.asset.id: c for c in clips}

        return [
            cluster
            for cluster_id, group_ids in enumerate(groups)
            if (
                cluster := self._build_cluster_from_group(
                    cluster_id, group_ids, clip_lookup, hashes
                )
            )
            is not None
        ]

    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """Compute Hamming distance between two hex hash strings."""
        try:
            int1 = int(hash1, 16)
            int2 = int(hash2, 16)
            xor = int1 ^ int2
            return bin(xor).count("1")
        except (ValueError, TypeError):
            return 64  # Maximum distance if hashes are invalid

    def _union_find(
        self,
        asset_ids: list[str],
        pairs: list[tuple[str, str, int]],
    ) -> list[set[str]]:
        """Group assets using union-find algorithm.

        Args:
            asset_ids: All asset IDs.
            pairs: List of (id1, id2, distance) tuples for similar pairs.

        Returns:
            List of sets, each containing asset IDs in a cluster.
        """
        # Initialize parent mapping
        parent = {aid: aid for aid in asset_ids}

        def find(x: str) -> str:
            if parent[x] != x:
                parent[x] = find(parent[x])  # Path compression
            return parent[x]

        def union(x: str, y: str) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Union similar pairs
        for id1, id2, _ in pairs:
            union(id1, id2)

        # Group by root
        groups: dict[str, set[str]] = {}
        for aid in asset_ids:
            root = find(aid)
            if root not in groups:
                groups[root] = set()
            groups[root].add(aid)

        return list(groups.values())

    def get_excluded_ids(self, clusters: list[DuplicateCluster]) -> set[str]:
        """Get asset IDs that should be excluded (duplicates, not best).

        Args:
            clusters: List of duplicate clusters.

        Returns:
            Set of asset IDs to exclude.
        """
        excluded = set()
        for cluster in clusters:
            for aid in cluster.asset_ids:
                if aid != cluster.best_asset_id:
                    excluded.add(aid)
        return excluded
