"""Tests for analysis/pipeline.py duplicate clustering behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

from immich_memories.analysis.pipeline import ClusterManager, DuplicateCluster
from tests.conftest import make_clip


def _make_cache(hashes: dict[str, str]) -> MagicMock:
    """Build a mock cache returning canned hashes.

    WHY: VideoAnalysisCache is an I/O boundary (SQLite). We mock it to
    test clustering logic without a real database.
    """
    cache = MagicMock()
    cache.get_all_hashes.return_value = hashes
    return cache


class TestBuildClusters:
    def test_similar_clips_grouped_into_cluster(self):
        """Two clips with identical hashes should be in the same cluster."""
        h = "abcdef1234567890"
        cache = _make_cache({"a": h, "b": h})
        clips = [make_clip("a", width=1920, height=1080), make_clip("b", width=1280, height=720)]

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert len(clusters) == 1
        assert set(clusters[0].asset_ids) == {"a", "b"}

    def test_different_clips_not_clustered(self):
        """Clips with maximally different hashes should produce no clusters."""
        cache = _make_cache({"a": "0000000000000000", "b": "ffffffffffffffff"})
        clips = [make_clip("a"), make_clip("b")]

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert clusters == []

    def test_best_is_highest_resolution(self):
        """The 'best' in a cluster should be the highest-resolution clip."""
        h = "abcdef1234567890"
        cache = _make_cache({"lo": h, "hi": h})
        clips = [
            make_clip("lo", width=640, height=480),
            make_clip("hi", width=3840, height=2160),
        ]

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert clusters[0].best_asset_id == "hi"

    def test_transitive_grouping(self):
        """If A~B and B~C, all three should be in one cluster."""
        # A and B: distance 2, B and C: distance 2, A and C: distance 4
        cache = _make_cache(
            {
                "a": "0000000000000000",
                "b": "0000000000000003",
                "c": "0000000000000005",
            }
        )
        clips = [make_clip("a"), make_clip("b"), make_clip("c")]

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert len(clusters) == 1
        assert set(clusters[0].asset_ids) == {"a", "b", "c"}


class TestBuildClustersEdgeCases:
    def test_empty_clips_returns_empty(self):
        cache = _make_cache({})
        clusters = ClusterManager(cache).build_clusters([], threshold=8)
        assert clusters == []

    def test_single_clip_returns_empty(self):
        cache = _make_cache({"a": "abcdef1234567890"})
        clusters = ClusterManager(cache).build_clusters([make_clip("a")], threshold=8)
        assert clusters == []

    def test_invalid_hash_treated_as_different(self):
        """Non-hex hash strings should not crash — treated as max distance."""
        cache = _make_cache({"a": "not_a_hex_hash", "b": "also_not_hex"})
        clips = [make_clip("a"), make_clip("b")]

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert clusters == []  # distance=64, well above threshold=8

    def test_clip_without_hash_excluded_from_clustering(self):
        """Clips not in the cache should be ignored, not crash."""
        cache = _make_cache({"a": "abcdef1234567890"})  # only "a" has hash
        clips = [make_clip("a"), make_clip("b")]  # "b" has no hash

        clusters = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert clusters == []  # only 1 clip with hash → no cluster


class TestThresholdSensitivity:
    def test_strict_threshold_fewer_clusters(self):
        """Stricter threshold should produce fewer clusters."""
        # Distance between these hashes: 4 bits differ (0x0f = 0b00001111)
        cache = _make_cache({"a": "0000000000000000", "b": "000000000000000f"})
        clips = [make_clip("a"), make_clip("b")]

        strict = ClusterManager(cache).build_clusters(clips, threshold=2)
        loose = ClusterManager(cache).build_clusters(clips, threshold=8)

        assert len(strict) == 0  # distance=4, above threshold=2
        assert len(loose) == 1  # distance=4, below threshold=8


class TestGetExcludedIds:
    def test_collects_all_non_best(self):
        clusters = [
            DuplicateCluster(cluster_id=0, asset_ids=["a", "b", "c"], best_asset_id="a"),
            DuplicateCluster(cluster_id=1, asset_ids=["d", "e"], best_asset_id="d"),
        ]
        cache = _make_cache({})

        excluded = ClusterManager(cache).get_excluded_ids(clusters)

        assert excluded == {"b", "c", "e"}

    def test_empty_clusters_returns_empty_set(self):
        cache = _make_cache({})
        assert ClusterManager(cache).get_excluded_ids([]) == set()
