"""Tests for duplicate detection."""

from __future__ import annotations

from datetime import datetime

from immich_memories.analysis.duplicates import (
    DuplicateGroup,
    _union_find_groups,
    hamming_distance,
)
from immich_memories.api.models import Asset, AssetType, VideoClipInfo


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
