"""Tests for video download cache."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.cache.video_cache import CachedVideo, VideoDownloadCache


@pytest.fixture
def cache_dir(tmp_path):
    """Provide a temporary cache directory."""
    return tmp_path / "video-cache"


@pytest.fixture
def cache(cache_dir):
    """Create a VideoDownloadCache with temporary directory."""
    return VideoDownloadCache(
        cache_dir=cache_dir,
        max_size_gb=1.0,
        max_age_days=7,
    )


@pytest.fixture
def mock_asset():
    """Create a mock Asset object."""
    asset = MagicMock()
    asset.id = "abc12345-6789-0abc-def0-123456789abc"
    asset.original_file_name = "vacation.MOV"
    asset.live_photo_video_id = None
    return asset


@pytest.fixture
def mock_client(tmp_path):
    """Create a mock Immich client that writes a fake video file on download."""

    def fake_download(asset_id: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video-data-" * 100)
        return output_path

    client = MagicMock()
    client.download_asset = MagicMock(side_effect=fake_download)
    return client


class TestVideoDownloadCacheInit:
    """Tests for cache initialization."""

    def test_creates_cache_dir(self, cache_dir):
        """Cache directory is created on init."""
        VideoDownloadCache(cache_dir=cache_dir)
        assert cache_dir.exists()

    def test_default_settings(self, cache_dir):
        """Default max_size_gb and max_age_days are sensible."""
        cache = VideoDownloadCache(cache_dir=cache_dir)
        assert cache.max_size_gb >= 1.0
        assert cache.max_age_days >= 1


class TestDownloadOrGet:
    """Tests for download_or_get method."""

    def test_downloads_when_not_cached(self, cache, mock_client, mock_asset):
        """Downloads video when not in cache."""
        path = cache.download_or_get(mock_client, mock_asset)

        assert path is not None
        assert path.exists()
        mock_client.download_asset.assert_called_once()

    def test_returns_cached_on_second_call(self, cache, mock_client, mock_asset):
        """Returns cached path without re-downloading."""
        path1 = cache.download_or_get(mock_client, mock_asset)
        path2 = cache.download_or_get(mock_client, mock_asset)

        assert path1 == path2
        assert mock_client.download_asset.call_count == 1

    def test_uses_two_level_directory(self, cache, mock_client, mock_asset):
        """Files are stored in {id[:2]}/{id}{ext} structure."""
        path = cache.download_or_get(mock_client, mock_asset)

        assert path.parent.name == mock_asset.id[:2]

    def test_preserves_file_extension(self, cache, mock_client, mock_asset):
        """File extension is preserved from original_file_name."""
        path = cache.download_or_get(mock_client, mock_asset)
        assert path.suffix.lower() == ".mov"

    def test_handles_download_failure(self, cache, mock_asset):
        """Returns None when download fails."""
        client = MagicMock()
        client.download_asset = MagicMock(side_effect=OSError("network error"))

        path = cache.download_or_get(client, mock_asset)
        assert path is None


class TestGetStats:
    """Tests for get_stats method."""

    def test_empty_cache_stats(self, cache):
        """Empty cache returns zero counts."""
        stats = cache.get_stats()
        assert stats["file_count"] == 0
        assert stats["total_size_bytes"] == 0
        assert stats["max_size_gb"] == 1.0

    def test_stats_after_download(self, cache, mock_client, mock_asset):
        """Stats reflect downloaded files."""
        cache.download_or_get(mock_client, mock_asset)
        stats = cache.get_stats()
        assert stats["file_count"] == 1
        assert stats["total_size_bytes"] > 0


class TestClear:
    """Tests for clear method."""

    def test_clear_empty_returns_zero(self, cache):
        """Clearing empty cache returns 0."""
        assert cache.clear() == 0

    def test_clear_removes_files(self, cache, mock_client, mock_asset):
        """Clear removes all cached files and returns count."""
        cache.download_or_get(mock_client, mock_asset)
        count = cache.clear()
        assert count == 1

        stats = cache.get_stats()
        assert stats["file_count"] == 0


class TestGetAnalysisVideo:
    """Tests for get_analysis_video method."""

    def test_returns_same_path_when_no_downscaling(self, cache, mock_client, mock_asset):
        """Without downscaling, both paths are the same."""
        analysis, original = cache.get_analysis_video(
            mock_client, mock_asset, target_height=480, enable_downscaling=False
        )
        assert analysis == original
        assert analysis.exists()

    def test_downloads_if_not_cached(self, cache, mock_client, mock_asset):
        """Downloads the video if not already cached."""
        analysis, original = cache.get_analysis_video(
            mock_client, mock_asset, target_height=480, enable_downscaling=False
        )
        mock_client.download_asset.assert_called_once()


class TestFindCachedExcludesDownscaled:
    """_find_cached must never return analysis-downscaled files."""

    def test_skips_480p_files(self, cache, mock_client, mock_asset):
        """If only a _480p file exists in cache, _find_cached returns None."""
        asset_id = mock_asset.id
        subdir = cache.cache_dir / asset_id[:2]
        subdir.mkdir(parents=True, exist_ok=True)

        # Simulate a leftover analysis downscale file
        downscaled = subdir / f"{asset_id}_480p.mp4"
        downscaled.write_bytes(b"low-quality-data" * 100)

        result = cache._find_cached(asset_id)
        assert result is None, f"Should not return downscaled file: {result}"

    def test_returns_original_when_both_exist(self, cache, mock_client, mock_asset):
        """When both original and _480p exist, returns the original."""
        asset_id = mock_asset.id
        subdir = cache.cache_dir / asset_id[:2]
        subdir.mkdir(parents=True, exist_ok=True)

        original = subdir / f"{asset_id}.MOV"
        original.write_bytes(b"original-video-data" * 100)
        downscaled = subdir / f"{asset_id}_480p.mp4"
        downscaled.write_bytes(b"low-quality-data" * 100)

        result = cache._find_cached(asset_id)
        assert result == original

    def test_skips_any_height_downscale(self, cache, mock_client, mock_asset):
        """Also skips _720p, _360p etc. downscale suffixes."""
        asset_id = mock_asset.id
        subdir = cache.cache_dir / asset_id[:2]
        subdir.mkdir(parents=True, exist_ok=True)

        downscaled = subdir / f"{asset_id}_720p.mp4"
        downscaled.write_bytes(b"low-quality-data" * 100)

        result = cache._find_cached(asset_id)
        assert result is None


class TestEvictOld:
    """Tests for age-based eviction."""

    def test_evicts_files_older_than_max_age(self, cache_dir):
        """Files older than max_age_days are removed."""
        import os

        cache = VideoDownloadCache(cache_dir=cache_dir, max_age_days=1)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True, exist_ok=True)

        old_file = subdir / "ab_old_video.mp4"
        old_file.write_bytes(b"old-data" * 100)
        # WHY: set mtime to 3 days ago so it exceeds max_age_days=1
        old_mtime = time.time() - (3 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        new_file = subdir / "ab_new_video.mp4"
        new_file.write_bytes(b"new-data" * 100)

        count = cache.evict_old()
        assert count == 1
        assert not old_file.exists()
        assert new_file.exists()


class TestEvictIfOverLimit:
    """Tests for size-based eviction."""

    def test_evicts_oldest_files_when_over_limit(self, cache_dir):
        """When cache exceeds max_size_gb, oldest files are removed first."""
        import os

        # 1 KB limit so test data triggers eviction
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True, exist_ok=True)

        old_file = subdir / "ab_oldest.mp4"
        old_file.write_bytes(b"x" * 1000)
        # WHY: force oldest mtime so this file is evicted first
        os.utime(old_file, (1000, 1000))

        new_file = subdir / "ab_newest.mp4"
        new_file.write_bytes(b"y" * 500)

        count = cache.evict_if_over_limit()
        assert count >= 1
        assert not old_file.exists()

    def test_no_eviction_when_under_limit(self, cache_dir):
        """No files removed when cache is within size limit."""
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=10.0)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True, exist_ok=True)

        f = subdir / "ab_small.mp4"
        f.write_bytes(b"small" * 10)

        count = cache.evict_if_over_limit()
        assert count == 0
        assert f.exists()

    def test_download_or_get_triggers_size_eviction(self, cache_dir):
        """download_or_get calls evict_if_over_limit after downloading."""
        import os

        # Tiny limit to force eviction
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)

        # Pre-populate with an old file
        subdir = cache_dir / "zz"
        subdir.mkdir(parents=True, exist_ok=True)
        old_file = subdir / "zz_old.mp4"
        old_file.write_bytes(b"old" * 1000)
        os.utime(old_file, (1000, 1000))

        # WHY: mock Immich client — unit test must not do real HTTP downloads
        asset = MagicMock()
        asset.id = "ab12345-new-asset"
        asset.original_file_name = "new.mp4"
        asset.live_photo_video_id = None

        def fake_download(asset_id: str, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"new-data" * 100)
            return output_path

        client = MagicMock()
        client.download_asset = MagicMock(side_effect=fake_download)

        path = cache.download_or_get(client, asset)
        assert path is not None
        assert path.exists()
        # Old file should have been evicted due to size pressure
        assert not old_file.exists()


class TestCachedVideo:
    """Tests for CachedVideo dataclass."""

    def test_cached_video_fields(self):
        """CachedVideo stores path and asset_id."""
        cv = CachedVideo(path=Path("/tmp/test.mp4"), asset_id="abc123")
        assert cv.path == Path("/tmp/test.mp4")
        assert cv.asset_id == "abc123"
