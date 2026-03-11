"""Tests for duplicate detection."""

from __future__ import annotations

from datetime import datetime

import pytest

try:
    import cv2  # noqa: F401
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)

from unittest.mock import patch

from immich_memories.analysis.duplicates import (
    DuplicateGroup,
    VideoHash,
    _union_find_groups,
    find_duplicate_groups,
    hamming_distance,
    rank_videos_by_quality,
)
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from tests.conftest import make_clip


class TestHammingDistance:
    """Tests for Hamming distance calculation."""

    def test_identical_hashes(self):
        """Test distance between identical hashes."""
        assert hamming_distance("abcd1234", "abcd1234") == 0

    def test_different_hashes(self):
        """Test distance between different hashes."""
        # These differ by one hex digit
        assert hamming_distance("abcd1234", "abcd1235") > 0

    def test_incompatible_lengths(self):
        """Test hashes with different lengths."""
        assert hamming_distance("abcd", "abcdef") == 64

    def test_complete_opposites(self):
        """Test maximally different hashes."""
        # 0 and f are bit opposites in hex
        dist = hamming_distance("0000", "ffff")
        assert dist == 16  # 4 hex digits * 4 bits each

    def test_empty_hashes(self):
        """Empty strings return max distance."""
        assert hamming_distance("", "") == 64

    def test_symmetry(self):
        """Distance is symmetric: d(a,b) == d(b,a)."""
        assert hamming_distance("abcd", "1234") == hamming_distance("1234", "abcd")


class TestUnionFindGroups:
    """Tests for union-find grouping."""

    def test_no_pairs(self):
        """Test with no pairs - each item in its own group."""
        items = ["a", "b", "c"]
        pairs = []
        groups = _union_find_groups(items, pairs)
        assert len(groups) == 3

    def test_single_pair(self):
        """Test with a single pair."""
        items = ["a", "b", "c"]
        pairs = [("a", "b")]
        groups = _union_find_groups(items, pairs)
        assert len(groups) == 2  # (a, b) and (c)

    def test_chain(self):
        """Test chained pairs."""
        items = ["a", "b", "c"]
        pairs = [("a", "b"), ("b", "c")]
        groups = _union_find_groups(items, pairs)
        assert len(groups) == 1  # All connected

    def test_two_groups(self):
        """Test two separate groups."""
        items = ["a", "b", "c", "d"]
        pairs = [("a", "b"), ("c", "d")]
        groups = _union_find_groups(items, pairs)
        assert len(groups) == 2


class TestDuplicateGroup:
    """Tests for DuplicateGroup."""

    def create_clip(
        self, asset_id: str, width: int, height: int, bitrate: int = 0
    ) -> VideoClipInfo:
        """Create a test video clip."""
        asset = Asset(
            id=asset_id,
            type=AssetType.VIDEO,
            fileCreatedAt=datetime.now(),
            fileModifiedAt=datetime.now(),
            updatedAt=datetime.now(),
        )
        return VideoClipInfo(
            asset=asset,
            width=width,
            height=height,
            bitrate=bitrate,
            duration_seconds=30,
        )

    def test_single_video(self):
        """Test group with single video."""
        clip = self.create_clip("v1", 1920, 1080)
        group = DuplicateGroup(videos=[clip])
        assert group.best_video_id == "v1"
        assert group.best_video == clip
        assert group.other_videos == []

    def test_best_video_by_resolution(self):
        """Test that higher resolution is preferred."""
        clip_low = self.create_clip("v1", 1280, 720)
        clip_high = self.create_clip("v2", 1920, 1080)
        group = DuplicateGroup(videos=[clip_low, clip_high])
        assert group.best_video_id == "v2"

    def test_best_video_by_bitrate(self):
        """Test that higher bitrate is preferred for same resolution."""
        clip_low = self.create_clip("v1", 1920, 1080, bitrate=5_000_000)
        clip_high = self.create_clip("v2", 1920, 1080, bitrate=20_000_000)
        group = DuplicateGroup(videos=[clip_low, clip_high])
        assert group.best_video_id == "v2"

    def test_other_videos(self):
        """Test getting non-best videos."""
        clips = [
            self.create_clip("v1", 1280, 720),
            self.create_clip("v2", 1920, 1080),
            self.create_clip("v3", 1280, 720),
        ]
        group = DuplicateGroup(videos=clips)
        assert group.best_video_id == "v2"
        other_ids = {v.asset.id for v in group.other_videos}
        assert other_ids == {"v1", "v3"}

    def test_to_dict(self):
        """Test dictionary conversion."""
        clips = [
            self.create_clip("v1", 1920, 1080),
            self.create_clip("v2", 1280, 720),
        ]
        group = DuplicateGroup(videos=clips)
        d = group.to_dict()
        assert set(d["video_ids"]) == {"v1", "v2"}
        assert d["best_video_id"] in {"v1", "v2"}


class TestVideoHash:
    """Tests for VideoHash dataclass."""

    def test_identical_hashes_distance_zero(self):
        """Same hash value gives distance 0."""
        h1 = VideoHash(asset_id="a", hash_value="abcd1234")
        h2 = VideoHash(asset_id="b", hash_value="abcd1234")
        assert h1.hamming_distance(h2) == 0

    def test_different_length_returns_max(self):
        """Incompatible hash lengths return 64."""
        h1 = VideoHash(asset_id="a", hash_value="abc")
        h2 = VideoHash(asset_id="b", hash_value="abcdef")
        assert h1.hamming_distance(h2) == 64

    def test_character_difference_counted(self):
        """Each differing character adds 1 to distance."""
        h1 = VideoHash(asset_id="a", hash_value="aaaa")
        h2 = VideoHash(asset_id="b", hash_value="aabb")
        assert h1.hamming_distance(h2) == 2


class TestUnionFindEdgeCases:
    """Edge cases for union-find."""

    def test_empty_items(self):
        """Empty items list returns empty groups."""
        assert _union_find_groups([], []) == []

    def test_single_item_no_pairs(self):
        """Single item returns one single-item group."""
        groups = _union_find_groups(["x"], [])
        assert len(groups) == 1
        assert groups[0] == ["x"]

    def test_all_connected(self):
        """All items connected produce one group."""
        items = ["a", "b", "c", "d"]
        pairs = [("a", "b"), ("b", "c"), ("c", "d")]
        groups = _union_find_groups(items, pairs)
        assert len(groups) == 1
        assert set(groups[0]) == {"a", "b", "c", "d"}


class TestFindDuplicateGroups:
    """Tests for find_duplicate_groups function."""

    def test_no_video_paths_returns_singles(self, sample_config):
        """Without video_paths, every video is its own group."""
        clips = [make_clip("v1"), make_clip("v2")]
        groups = find_duplicate_groups(clips)
        assert len(groups) == 2
        assert all(len(g.videos) == 1 for g in groups)

    def test_empty_videos_returns_empty(self, sample_config):
        """Empty input returns empty list."""
        groups = find_duplicate_groups([])
        assert groups == []

    @patch("immich_memories.analysis.duplicates.compute_video_hash")
    def test_identical_hashes_grouped(self, mock_hash, sample_config, tmp_path):
        """Videos with identical hashes are grouped together."""
        mock_hash.return_value = "abcdef1234567890"
        c1 = make_clip("v1")
        c2 = make_clip("v2")
        p1 = tmp_path / "v1.mp4"
        p2 = tmp_path / "v2.mp4"
        p1.write_bytes(b"\x00")
        p2.write_bytes(b"\x00")

        groups = find_duplicate_groups(
            [c1, c2],
            threshold=5,
            video_paths={"v1": p1, "v2": p2},
        )
        # Both should be in same group
        multi_groups = [g for g in groups if len(g.videos) > 1]
        assert len(multi_groups) == 1
        assert {v.asset.id for v in multi_groups[0].videos} == {"v1", "v2"}

    @patch("immich_memories.analysis.duplicates.compute_video_hash")
    def test_different_hashes_separate(self, mock_hash, sample_config, tmp_path):
        """Videos with very different hashes stay separate."""
        # Return different hashes for different calls
        mock_hash.side_effect = ["aaaaaaaaaaaaaaaa", "zzzzzzzzzzzzzzzz"]
        c1 = make_clip("v1")
        c2 = make_clip("v2")
        p1 = tmp_path / "v1.mp4"
        p2 = tmp_path / "v2.mp4"
        p1.write_bytes(b"\x00")
        p2.write_bytes(b"\x00")

        groups = find_duplicate_groups(
            [c1, c2],
            threshold=2,
            video_paths={"v1": p1, "v2": p2},
        )
        assert all(len(g.videos) == 1 for g in groups)

    @patch("immich_memories.analysis.duplicates.compute_video_hash")
    def test_missing_path_video_ungrouped(self, mock_hash, sample_config, tmp_path):
        """Videos without paths appear as single-item groups."""
        mock_hash.return_value = "abcdef1234567890"
        c1 = make_clip("v1")
        c2 = make_clip("v2")
        p1 = tmp_path / "v1.mp4"
        p1.write_bytes(b"\x00")

        groups = find_duplicate_groups(
            [c1, c2],
            threshold=5,
            video_paths={"v1": p1},  # v2 missing
        )
        # v2 should appear as ungrouped single
        ids_in_groups = [v.asset.id for g in groups for v in g.videos]
        assert "v1" in ids_in_groups
        assert "v2" in ids_in_groups


class TestRankVideosByQuality:
    """Tests for rank_videos_by_quality."""

    def test_sorts_by_resolution(self):
        """Higher resolution ranks first."""
        low = make_clip("low", width=1280, height=720)
        high = make_clip("high", width=1920, height=1080)
        ranked = rank_videos_by_quality([low, high])
        assert ranked[0].asset.id == "high"

    def test_same_resolution_sorts_by_bitrate(self):
        """Same resolution sorts by bitrate."""
        low_br = make_clip("low", bitrate=5_000_000)
        high_br = make_clip("high", bitrate=20_000_000)
        ranked = rank_videos_by_quality([low_br, high_br])
        assert ranked[0].asset.id == "high"

    def test_empty_list(self):
        """Empty input returns empty list."""
        assert rank_videos_by_quality([]) == []

    def test_single_video(self):
        """Single video returns that video."""
        clip = make_clip("only")
        result = rank_videos_by_quality([clip])
        assert len(result) == 1
        assert result[0].asset.id == "only"
