"""Tests for thumbnail clustering and deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    import cv2  # noqa: F401
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.thumbnail_clustering import (
    ThumbnailCluster,
    _build_similarity_pairs,
    _compute_thumbnail_hashes,
    _groups_to_clusters,
    _select_cluster_representative,
)
from tests.conftest import make_clip


class TestBuildSimilarityPairs:
    """Tests for hash-based similarity pairing."""

    def test_identical_hashes_paired(self):
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=10.0)
        hashes = {"a": "abcd1234abcd1234", "b": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert ("a", "b") in pairs

    def test_different_hashes_not_paired(self):
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=10.0)
        # Maximally different hashes → distance 64
        hashes = {"a": "0000000000000000", "b": "ffffffffffffffff"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert len(pairs) == 0

    def test_duration_gate_rejects_different_durations(self):
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=30.0)  # 200% different
        hashes = {"a": "abcd1234abcd1234", "b": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert len(pairs) == 0

    def test_duration_gate_allows_similar_durations(self):
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=11.0)  # 10% different, under 20%
        hashes = {"a": "abcd1234abcd1234", "b": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert ("a", "b") in pairs

    def test_duration_gate_boundary(self):
        # Exactly 20% difference: |10 - 12| / 12 = 0.1667 < 0.2 → allowed
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=12.0)
        hashes = {"a": "abcd1234abcd1234", "b": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert ("a", "b") in pairs

    def test_duration_gate_just_over_boundary(self):
        # |10 - 13| / 13 = 0.2308 > 0.2 → rejected
        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=13.0)
        hashes = {"a": "abcd1234abcd1234", "b": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=5)
        assert len(pairs) == 0

    def test_threshold_boundary(self):
        # Use real hamming_distance to verify threshold behavior
        hash_a = "abcd1234abcd1234"
        hash_b = "abcd1234abcd1235"  # Slightly different
        dist = hamming_distance(hash_a, hash_b)

        clip_a = make_clip("a", duration=10.0)
        clip_b = make_clip("b", duration=10.0)
        hashes = {"a": hash_a, "b": hash_b}

        # With threshold exactly at the distance → should be paired
        pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=dist)
        assert ("a", "b") in pairs

        # With threshold one below → should NOT be paired
        if dist > 0:
            pairs = _build_similarity_pairs(hashes, [clip_a, clip_b], threshold=dist - 1)
            assert len(pairs) == 0

    def test_multiple_clips_pairwise(self):
        clips = [make_clip(f"c{i}", duration=10.0) for i in range(3)]
        same_hash = "abcd1234abcd1234"
        hashes = {"c0": same_hash, "c1": same_hash, "c2": same_hash}
        pairs = _build_similarity_pairs(hashes, clips, threshold=5)
        # 3 clips → 3 pairs: (c0,c1), (c0,c2), (c1,c2)
        assert len(pairs) == 3

    def test_empty_hashes(self):
        pairs = _build_similarity_pairs({}, [], threshold=5)
        assert pairs == []

    def test_single_clip(self):
        clip = make_clip("only", duration=10.0)
        hashes = {"only": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [clip], threshold=5)
        assert pairs == []

    def test_clips_missing_from_duration_lookup(self):
        # Clip IDs in hashes but not in clips list — duration defaults to 0
        hashes = {"x": "abcd1234abcd1234", "y": "abcd1234abcd1234"}
        pairs = _build_similarity_pairs(hashes, [], threshold=5)
        # Both durations are 0, |0-0|/0.1 = 0 < 0.2 → paired
        assert ("x", "y") in pairs


class TestSelectClusterRepresentative:
    """Tests for cluster representative selection."""

    def test_single_clip_returns_itself(self):
        clip = make_clip("solo")
        result = _select_cluster_representative([clip])
        assert result.asset.id == "solo"

    def test_favorite_preferred(self):
        normal = make_clip("normal", is_favorite=False)
        fav = make_clip("fav", is_favorite=True)
        result = _select_cluster_representative([normal, fav])
        assert result.asset.id == "fav"

    def test_higher_resolution_wins(self):
        low_res = make_clip("low", width=1280, height=720)
        high_res = make_clip("high", width=3840, height=2160)
        result = _select_cluster_representative([low_res, high_res])
        assert result.asset.id == "high"

    def test_favorite_beats_resolution(self):
        high_res = make_clip("high", width=3840, height=2160, is_favorite=False)
        fav_low = make_clip("fav", width=1280, height=720, is_favorite=True)
        result = _select_cluster_representative([high_res, fav_low])
        assert result.asset.id == "fav"

    def test_longer_duration_tiebreaker(self):
        short = make_clip("short", duration=5.0, width=1920, height=1080)
        long = make_clip("long", duration=15.0, width=1920, height=1080)
        result = _select_cluster_representative([short, long])
        assert result.asset.id == "long"

    def test_higher_bitrate_tiebreaker(self):
        low_br = make_clip("low_br", bitrate=5_000_000, width=1920, height=1080)
        high_br = make_clip("high_br", bitrate=50_000_000, width=1920, height=1080)
        result = _select_cluster_representative([low_br, high_br])
        assert result.asset.id == "high_br"

    def test_zero_dimensions_handled(self):
        zero_dim = make_clip("zero", width=0, height=0)
        normal = make_clip("normal", width=1920, height=1080)
        result = _select_cluster_representative([zero_dim, normal])
        assert result.asset.id == "normal"


class TestGroupsToClusters:
    """Tests for converting union-find groups to ThumbnailCluster objects."""

    def test_single_group(self):
        clips = [make_clip("a"), make_clip("b")]
        groups = [["a", "b"]]
        result = _groups_to_clusters(groups, clips)
        assert len(result) == 1
        assert set(result[0].clip_ids) == {"a", "b"}

    def test_ungrouped_clips_become_singletons(self):
        clips = [make_clip("a"), make_clip("b"), make_clip("c")]
        groups = [["a", "b"]]  # c is ungrouped
        result = _groups_to_clusters(groups, clips)
        assert len(result) == 2
        # One group of 2 + one singleton
        cluster_sizes = sorted(len(c.clip_ids) for c in result)
        assert cluster_sizes == [1, 2]

    def test_all_ungrouped(self):
        clips = [make_clip("a"), make_clip("b")]
        groups: list[list[str]] = []
        result = _groups_to_clusters(groups, clips)
        assert len(result) == 2
        for cluster in result:
            assert len(cluster.clip_ids) == 1
            assert cluster.representative_id == cluster.clip_ids[0]

    def test_missing_clip_ids_skipped(self):
        clips = [make_clip("a")]
        groups = [["a", "missing_id"]]
        result = _groups_to_clusters(groups, clips)
        assert len(result) == 1
        # Group still lists both IDs, but representative is from available clips
        assert result[0].representative_id == "a"

    def test_empty_group_skipped(self):
        clips = [make_clip("a")]
        # A group where all IDs are missing from lookup
        groups = [["nonexistent1", "nonexistent2"]]
        result = _groups_to_clusters(groups, clips)
        # The empty group produces no cluster, but "a" becomes a singleton
        assert len(result) == 1
        assert result[0].representative_id == "a"

    def test_representative_selection_applied(self):
        fav = make_clip("fav", is_favorite=True)
        normal = make_clip("normal", is_favorite=False)
        groups = [["fav", "normal"]]
        result = _groups_to_clusters(groups, [fav, normal])
        assert result[0].representative_id == "fav"

    def test_multiple_groups(self):
        clips = [make_clip(f"c{i}") for i in range(4)]
        groups = [["c0", "c1"], ["c2", "c3"]]
        result = _groups_to_clusters(groups, clips)
        assert len(result) == 2


class TestComputeThumbnailHashes:
    """Tests for thumbnail hash computation with mock cache."""

    def test_returns_hashes_for_cached_thumbnails(self):
        clip_a = make_clip("a")
        clip_b = make_clip("b")

        # WHY: mock thumbnail_cache to avoid real disk/network I/O
        cache = MagicMock()
        # Create a tiny valid JPEG-like image for compute_thumbnail_hash
        import numpy as np

        img = np.zeros((8, 8, 3), dtype=np.uint8)
        _, jpeg_bytes = cv2.imencode(".jpg", img)
        cache.get.return_value = jpeg_bytes.tobytes()

        hashes = _compute_thumbnail_hashes([clip_a, clip_b], cache)
        assert "a" in hashes
        assert "b" in hashes
        assert len(hashes["a"]) > 0
        assert len(hashes["b"]) > 0

    def test_skips_clips_without_thumbnails(self):
        clip_a = make_clip("a")
        clip_b = make_clip("b")

        # WHY: mock thumbnail_cache to control which clips have thumbnails
        cache = MagicMock()
        import numpy as np

        img = np.zeros((8, 8, 3), dtype=np.uint8)
        _, jpeg_bytes = cv2.imencode(".jpg", img)

        def get_side_effect(asset_id: str, kind: str) -> bytes | None:
            if asset_id == "a":
                return jpeg_bytes.tobytes()
            return None

        cache.get.side_effect = get_side_effect

        hashes = _compute_thumbnail_hashes([clip_a, clip_b], cache)
        assert "a" in hashes
        assert "b" not in hashes

    def test_progress_callback_called(self):
        clips = [make_clip(f"c{i}") for i in range(3)]

        # WHY: mock thumbnail_cache — not testing real cache behavior
        cache = MagicMock()
        cache.get.return_value = None

        progress_calls: list[tuple[int, int]] = []

        def callback(current: int, total: int) -> None:
            progress_calls.append((current, total))

        _compute_thumbnail_hashes(clips, cache, progress_callback=callback)
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3)
        assert progress_calls[1] == (2, 3)
        assert progress_calls[2] == (3, 3)

    def test_empty_clips_list(self):
        cache = MagicMock()
        hashes = _compute_thumbnail_hashes([], cache)
        assert hashes == {}

    def test_no_progress_callback(self):
        clip = make_clip("a")
        # WHY: mock thumbnail_cache — not testing real cache behavior
        cache = MagicMock()
        cache.get.return_value = None
        # Should not raise when progress_callback is None
        hashes = _compute_thumbnail_hashes([clip], cache, progress_callback=None)
        assert hashes == {}


class TestThumbnailClusterDataclass:
    """Tests for ThumbnailCluster dataclass."""

    def test_fields(self):
        cluster = ThumbnailCluster(clip_ids=["a", "b"], representative_id="a")
        assert cluster.clip_ids == ["a", "b"]
        assert cluster.representative_id == "a"
