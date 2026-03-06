"""Tests for the video analysis cache."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.cache.database import (
    CachedSegment,
    CachedVideoAnalysis,
    VideoAnalysisCache,
    _hamming_distance,
)


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def cache(temp_db_path):
    """Create a cache instance with a temporary database."""
    return VideoAnalysisCache(temp_db_path)


@pytest.fixture
def mock_asset():
    """Create a mock Asset object."""
    asset = MagicMock()
    asset.id = "test-asset-123"
    asset.checksum = "abc123"
    asset.file_modified_at = datetime(2024, 1, 15, 12, 0, 0)
    asset.file_created_at = datetime(2024, 1, 15, 10, 0, 0)
    asset.duration_seconds = 30.0
    return asset


@pytest.fixture
def mock_video_info():
    """Create a mock VideoClipInfo object."""
    info = MagicMock()
    info.duration_seconds = 30.0
    info.width = 1920
    info.height = 1080
    info.bitrate = 10000000
    info.fps = 30.0
    info.codec = "h264"
    info.color_space = None
    info.color_transfer = None
    info.color_primaries = None
    info.bit_depth = 8
    return info


@pytest.fixture
def mock_moment_scores():
    """Create mock MomentScore objects."""
    from immich_memories.analysis.scoring import MomentScore

    return [
        MomentScore(
            start_time=0.0,
            end_time=5.0,
            total_score=0.7,
            face_score=0.8,
            motion_score=0.6,
            stability_score=0.7,
            audio_score=0.5,
            face_positions=[(0.5, 0.5), (0.3, 0.4)],
        ),
        MomentScore(
            start_time=5.0,
            end_time=10.0,
            total_score=0.5,
            face_score=0.4,
            motion_score=0.5,
            stability_score=0.6,
            audio_score=0.5,
        ),
    ]


class TestHammingDistance:
    """Tests for Hamming distance calculation."""

    def test_identical_hashes(self):
        """Identical hashes should have distance 0."""
        assert _hamming_distance("abcd1234", "abcd1234") == 0

    def test_completely_different(self):
        """Completely different hashes should have distance 64."""
        assert _hamming_distance("0000000000000000", "ffffffffffffffff") == 64

    def test_one_bit_difference(self):
        """One bit difference."""
        assert _hamming_distance("0000000000000000", "0000000000000001") == 1

    def test_invalid_hash(self):
        """Invalid hashes should return max distance."""
        assert _hamming_distance("invalid", "0000000000000000") == 64


class TestVideoAnalysisCache:
    """Tests for VideoAnalysisCache."""

    def test_database_creation(self, cache, temp_db_path):
        """Database file should be created."""
        assert temp_db_path.exists()

    def test_save_and_get_analysis(self, cache, mock_asset, mock_video_info, mock_moment_scores):
        """Should save and retrieve analysis."""
        # Save
        cache.save_analysis(
            asset=mock_asset,
            video_info=mock_video_info,
            perceptual_hash="abcd1234efgh5678",
            segments=mock_moment_scores,
        )

        # Retrieve
        analysis = cache.get_analysis(mock_asset.id)

        assert analysis is not None
        assert analysis.asset_id == mock_asset.id
        assert analysis.checksum == mock_asset.checksum
        assert analysis.perceptual_hash == "abcd1234efgh5678"
        assert analysis.width == 1920
        assert analysis.height == 1080
        assert analysis.best_total_score == 0.7

    def test_segments_saved(self, cache, mock_asset, mock_video_info, mock_moment_scores):
        """Segments should be saved and loaded."""
        cache.save_analysis(
            asset=mock_asset,
            video_info=mock_video_info,
            segments=mock_moment_scores,
        )

        analysis = cache.get_analysis(mock_asset.id, include_segments=True)

        assert len(analysis.segments) == 2
        assert analysis.segments[0].start_time == 0.0
        assert analysis.segments[0].end_time == 5.0
        assert analysis.segments[0].total_score == 0.7

    def test_get_nonexistent(self, cache):
        """Should return None for nonexistent asset."""
        analysis = cache.get_analysis("nonexistent-id")
        assert analysis is None

    def test_needs_reanalysis_not_cached(self, cache, mock_asset):
        """Uncached asset should need reanalysis."""
        assert cache.needs_reanalysis(mock_asset) is True

    def test_needs_reanalysis_cached(self, cache, mock_asset, mock_video_info):
        """Cached asset with same checksum should not need reanalysis."""
        cache.save_analysis(asset=mock_asset, video_info=mock_video_info)
        assert cache.needs_reanalysis(mock_asset) is False

    def test_needs_reanalysis_checksum_changed(self, cache, mock_asset, mock_video_info):
        """Cached asset with different checksum should need reanalysis."""
        cache.save_analysis(asset=mock_asset, video_info=mock_video_info)

        mock_asset.checksum = "different-checksum"
        assert cache.needs_reanalysis(mock_asset) is True

    def test_find_similar_videos(self, cache, mock_asset, mock_video_info):
        """Should find videos with similar hashes."""
        # Use valid hex hashes (0-9, a-f only)
        hash1 = "abcd1234abcd5678"

        # Save first video
        cache.save_analysis(
            asset=mock_asset,
            video_info=mock_video_info,
            perceptual_hash=hash1,
        )

        # Save second video with similar hash (shares some chunks)
        mock_asset2 = MagicMock()
        mock_asset2.id = "test-asset-456"
        mock_asset2.checksum = "def456"
        mock_asset2.file_modified_at = datetime(2024, 1, 15, 12, 0, 0)
        mock_asset2.file_created_at = datetime(2024, 1, 15, 10, 0, 0)

        # Use a hash that shares at least one 4-char chunk with the first hash
        # First hash chunks: abcd, 1234, abcd, 5678
        # This hash shares "abcd" chunk (position 0)
        hash2 = "abcd1234abcd5679"  # Only last digit different
        cache.save_analysis(
            asset=mock_asset2,
            video_info=mock_video_info,
            perceptual_hash=hash2,
        )

        # Find similar - query for first video, should find second
        similar = cache.find_similar_videos(
            hash1,
            threshold=8,
            exclude_asset_id=mock_asset.id,  # Exclude self
        )

        # Should find the second video
        assert len(similar) == 1
        assert similar[0].asset_id == "test-asset-456"
        assert similar[0].hamming_distance <= 8

    def test_get_uncached_asset_ids(self, cache, mock_asset, mock_video_info):
        """Should identify uncached assets."""
        cache.save_analysis(asset=mock_asset, video_info=mock_video_info)

        uncached = cache.get_uncached_asset_ids(
            ["test-asset-123", "test-asset-456", "test-asset-789"]
        )

        assert "test-asset-123" not in uncached
        assert "test-asset-456" in uncached
        assert "test-asset-789" in uncached

    def test_delete_analysis(self, cache, mock_asset, mock_video_info):
        """Should delete analysis."""
        cache.save_analysis(asset=mock_asset, video_info=mock_video_info)
        assert cache.get_analysis(mock_asset.id) is not None

        result = cache.delete_analysis(mock_asset.id)
        assert result is True
        assert cache.get_analysis(mock_asset.id) is None

    def test_clear_all(self, cache, mock_asset, mock_video_info):
        """Should clear all data."""
        cache.save_analysis(asset=mock_asset, video_info=mock_video_info)

        count = cache.clear_all()
        assert count == 1
        assert cache.get_analysis(mock_asset.id) is None

    def test_get_stats(self, cache, mock_asset, mock_video_info, mock_moment_scores):
        """Should return cache statistics."""
        cache.save_analysis(
            asset=mock_asset,
            video_info=mock_video_info,
            perceptual_hash="abcd1234efgh5678",
            segments=mock_moment_scores,
        )

        stats = cache.get_stats()

        assert stats["total_videos"] == 1
        assert stats["videos_with_hash"] == 1
        assert stats["total_segments"] == 2


class TestCachedSegment:
    """Tests for CachedSegment."""

    def test_duration(self):
        """Should calculate duration."""
        segment = CachedSegment(
            segment_index=0,
            start_time=5.0,
            end_time=10.0,
        )
        assert segment.duration == 5.0

    def test_to_moment_score(self):
        """Should convert to MomentScore."""
        segment = CachedSegment(
            segment_index=0,
            start_time=5.0,
            end_time=10.0,
            total_score=0.7,
            face_score=0.8,
            motion_score=0.6,
            stability_score=0.7,
            audio_score=0.5,
        )

        moment = segment.to_moment_score()

        assert moment.start_time == 5.0
        assert moment.end_time == 10.0
        assert moment.total_score == 0.7


class TestCachedVideoAnalysis:
    """Tests for CachedVideoAnalysis."""

    def test_get_best_segment(self):
        """Should return highest scoring segment."""
        analysis = CachedVideoAnalysis(
            asset_id="test",
            checksum=None,
            file_modified_at=None,
            analysis_timestamp=datetime.now(),
            segments=[
                CachedSegment(segment_index=0, start_time=0, end_time=5, total_score=0.5),
                CachedSegment(segment_index=1, start_time=5, end_time=10, total_score=0.8),
                CachedSegment(segment_index=2, start_time=10, end_time=15, total_score=0.6),
            ],
        )

        best = analysis.get_best_segment()

        assert best is not None
        assert best.segment_index == 1
        assert best.total_score == 0.8

    def test_get_best_segment_empty(self):
        """Should return None when no segments."""
        analysis = CachedVideoAnalysis(
            asset_id="test",
            checksum=None,
            file_modified_at=None,
            analysis_timestamp=datetime.now(),
        )

        assert analysis.get_best_segment() is None
