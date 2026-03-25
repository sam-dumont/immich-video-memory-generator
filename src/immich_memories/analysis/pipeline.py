"""Video analysis pipeline for batch processing and clustering."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache

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
                (clip_lookup[aid].width) * (clip_lookup[aid].height) if aid in clip_lookup else 0
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
            return xor.bit_count()
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
