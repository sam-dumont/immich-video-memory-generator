"""Tests for video download cache."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread
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

    def test_download_failure_log_does_not_include_raw_exception(self, cache, mock_asset, caplog):
        """Cache logs a safe failure diagnostic instead of server error text."""
        client = MagicMock()
        client.download_asset.side_effect = OSError("token=unlabelled-secret-value")

        assert cache.download_or_get(client, mock_asset) is None

        assert "unlabelled-secret-value" not in caplog.text


class TestCacheBatch:
    """One explicit batch owns maintenance for any number of downloads."""

    def test_twenty_downloads_scan_cache_once(self, cache, mock_client, monkeypatch):
        assets = []
        for index in range(20):
            asset = MagicMock()
            asset.id = f"asset-{index:02d}"
            asset.original_file_name = f"clip-{index:02d}.mp4"
            asset.live_photo_video_id = None
            assets.append(asset)

        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch:
            paths = [batch.download_or_get(mock_client, asset) for asset in assets]

        assert len(scans) == 1
        assert all(path is not None and path.exists() for path in paths)
        assert mock_client.download_asset.call_count == 20

    def test_failed_download_updates_batch_without_finish_rescan(
        self, cache, mock_asset, monkeypatch
    ):
        client = MagicMock()
        client.download_asset.side_effect = OSError("download failed")
        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch:
            assert batch.download_or_get(client, mock_asset) is None

        assert len(scans) == 1
        assert list(cache.cache_dir.rglob("*.*")) == []

    def test_propagated_download_error_updates_batch_without_finish_rescan(
        self, cache, mock_asset, monkeypatch
    ):
        client = MagicMock()

        def partial_download(_asset_id: str, output_path: Path) -> None:
            output_path.write_bytes(b"partial")
            raise ValueError("download size limit exceeded")

        client.download_asset.side_effect = partial_download
        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch, pytest.raises(ValueError, match="size limit"):
            batch.download_or_get(client, mock_asset)

        assert len(scans) == 1
        assert list(cache.cache_dir.rglob("*.*")) == []

    def test_context_finishes_size_eviction_after_item_exception(self, cache_dir):
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)
        old = cache_dir / "old" / "old.mp4"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"o" * 2_000)

        with pytest.raises(RuntimeError, match="item failed"), cache.begin_batch():
            raise RuntimeError("item failed")

        assert not old.exists()

    def test_new_download_is_added_to_manifest_and_evicted_at_finish(self, cache_dir, mock_asset):
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)
        client = MagicMock()

        def oversized_download(_asset_id: str, output_path: Path) -> Path:
            output_path.write_bytes(b"x" * 2_000)
            return output_path

        client.download_asset.side_effect = oversized_download
        with cache.begin_batch() as batch:
            downloaded = batch.download_or_get(client, mock_asset)
            assert downloaded is not None and downloaded.exists()

        assert not downloaded.exists()

    def test_finish_evicts_oldest_manifest_entry_first(self, cache_dir):
        import os

        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True)
        oldest = subdir / "oldest.mp4"
        oldest.write_bytes(b"o" * 800)
        older_mtime = time.time() - 3_600
        os.utime(oldest, (older_mtime, older_mtime))
        newest = subdir / "newest.mp4"
        newest.write_bytes(b"n" * 500)

        with cache.begin_batch():
            pass

        assert not oldest.exists()
        assert newest.exists()

    def test_cache_hit_refreshes_mtime_without_downloading(self, cache, mock_client, mock_asset):
        cached = cache._video_path(mock_asset.id, ".MOV")
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"cached-video")
        old_mtime = time.time() - 3_600
        import os

        os.utime(cached, (old_mtime, old_mtime))

        with cache.begin_batch() as batch:
            result = batch.download_or_get(mock_client, mock_asset)

        assert result == cached
        assert cached.stat().st_mtime > old_mtime
        mock_client.download_asset.assert_not_called()

    def test_cached_analysis_downscale_is_recorded_and_touched(self, cache, mock_asset):
        import os

        original = cache._video_path(mock_asset.id, ".MOV")
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original")
        downscaled = original.parent / f"{mock_asset.id}_480p.MOV"
        downscaled.write_bytes(b"downscaled" * 200)
        old_mtime = time.time() - 3_600
        os.utime(downscaled, (old_mtime, old_mtime))
        client = MagicMock()

        with cache.begin_batch() as batch:
            analysis, source = batch.get_analysis_video(client, mock_asset)

        assert (analysis, source) == (downscaled, original)
        assert downscaled.stat().st_mtime > old_mtime
        client.download_asset.assert_not_called()

    def test_record_path_rejects_files_outside_cache_root(self, cache, tmp_path):
        outside = tmp_path / "not-cache-owned.mp4"
        outside.write_bytes(b"external")

        with (
            cache.begin_batch() as batch,
            pytest.raises(ValueError, match="inside its cache root"),
        ):
            batch.record_path(outside)

        assert outside.read_bytes() == b"external"

    def test_external_mutation_triggers_one_fallback_scan(self, cache_dir, monkeypatch):
        cache = VideoDownloadCache(cache_dir=cache_dir, max_size_gb=0.000001)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True)
        original = subdir / "original.mp4"
        original.write_bytes(b"o" * 500)

        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch():
            external = subdir / "external.mp4"
            external.write_bytes(b"e" * 2_000)

        assert len(scans) == 2
        assert sum(path.stat().st_size for path in subdir.iterdir()) <= 1_073

    def test_start_scan_removes_expired_and_keeps_fresh_without_rescan(
        self, cache_dir, monkeypatch
    ):
        import os

        cache = VideoDownloadCache(cache_dir=cache_dir, max_age_days=1)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True)
        expired = subdir / "expired.mp4"
        expired.write_bytes(b"expired")
        expired_mtime = time.time() - (3 * 86_400)
        os.utime(expired, (expired_mtime, expired_mtime))
        fresh = subdir / "fresh.mp4"
        fresh.write_bytes(b"fresh")

        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch():
            pass

        assert not expired.exists()
        assert fresh.exists()
        assert len(scans) == 1

    def test_finish_closes_admission_and_waits_for_admitted_download(
        self, cache, mock_asset
    ) -> None:
        """Finish waits for in-flight network work, then rejects new operations."""
        started = Event()
        release = Event()
        completed = Event()
        client = MagicMock()

        def blocked_download(_asset_id: str, output_path: Path) -> Path:
            started.set()
            assert release.wait(timeout=2)
            output_path.write_bytes(b"video")
            return output_path

        client.download_asset.side_effect = blocked_download
        batch = cache.begin_batch()
        download_thread = Thread(target=batch.download_or_get, args=(client, mock_asset))
        download_thread.start()
        assert started.wait(timeout=2)

        finish_thread = Thread(target=lambda: (batch.finish(), completed.set()), daemon=True)
        finish_thread.start()
        time.sleep(0.05)
        assert not completed.is_set()
        with pytest.raises(RuntimeError, match="closed"):
            batch.download_or_get(client, mock_asset)

        release.set()
        download_thread.join(timeout=2)
        finish_thread.join(timeout=2)
        assert completed.is_set()
        with pytest.raises(RuntimeError, match="closed"):
            batch.download_or_get(client, mock_asset)

    def test_failed_admitted_download_unblocks_finish(self, cache, mock_asset) -> None:
        """An item exception still releases finish's in-flight operation wait."""
        started = Event()
        release = Event()
        completed = Event()
        client = MagicMock()

        def blocked_failure(_asset_id: str, _output_path: Path) -> None:
            started.set()
            assert release.wait(timeout=2)
            raise ValueError("download failed")

        client.download_asset.side_effect = blocked_failure
        batch = cache.begin_batch()

        def run_download() -> None:
            with pytest.raises(ValueError, match="download failed"):
                batch.download_or_get(client, mock_asset)

        download_thread = Thread(target=run_download)
        download_thread.start()
        assert started.wait(timeout=2)
        finish_thread = Thread(target=lambda: (batch.finish(), completed.set()))
        finish_thread.start()
        time.sleep(0.05)
        assert not completed.is_set()

        release.set()
        download_thread.join(timeout=2)
        finish_thread.join(timeout=2)
        assert completed.is_set()

    def test_admitted_analysis_operation_can_nest_after_finish_starts(
        self, cache, mock_asset, monkeypatch
    ) -> None:
        """Finish cannot reject the nested cache read of an already-admitted analysis call."""
        outer_started = Event()
        allow_nested_download = Event()
        nested_started = Event()
        completed = Event()
        client = MagicMock()

        def download(_asset_id: str, output_path: Path) -> Path:
            nested_started.set()
            output_path.write_bytes(b"video")
            return output_path

        client.download_asset.side_effect = download
        batch = cache.begin_batch()
        original_get_analysis_video = cache.get_analysis_video

        def delayed_get_analysis_video(*args, **kwargs):
            outer_started.set()
            assert allow_nested_download.wait(timeout=2)
            return original_get_analysis_video(*args, **kwargs)

        monkeypatch.setattr(cache, "get_analysis_video", delayed_get_analysis_video)
        analysis_thread = Thread(
            target=lambda: batch.get_analysis_video(client, mock_asset, enable_downscaling=False)
        )
        analysis_thread.start()
        assert outer_started.wait(timeout=2)
        finish_thread = Thread(target=lambda: (batch.finish(), completed.set()))
        finish_thread.start()
        time.sleep(0.05)
        allow_nested_download.set()

        analysis_thread.join(timeout=2)
        finish_thread.join(timeout=2)
        assert nested_started.is_set()
        assert completed.is_set()

    def test_concurrent_finish_calls_are_idempotent(self, cache, monkeypatch) -> None:
        """Only one finisher performs cache maintenance; the other returns cleanly."""
        batch = cache.begin_batch()
        started = Event()
        release = Event()
        original_evict = cache._evict_manifest
        calls = 0

        def blocked_evict(manifest):
            nonlocal calls
            calls += 1
            started.set()
            assert release.wait(timeout=2)
            return original_evict(manifest)

        monkeypatch.setattr(cache, "_evict_manifest", blocked_evict)
        first = Thread(target=batch.finish)
        second = Thread(target=batch.finish)
        first.start()
        assert started.wait(timeout=2)
        second.start()
        time.sleep(0.05)
        assert second.is_alive()
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        assert calls == 1

    def test_finish_waits_for_admitted_burst_component_download(self, cache) -> None:
        """Component downloads hold batch admission through their network body."""
        started = Event()
        release = Event()
        finished = Event()
        client = MagicMock()

        def blocked_download(_asset_id: str, output_path: Path) -> Path:
            started.set()
            assert release.wait(timeout=2)
            output_path.write_bytes(b"component")
            return output_path

        client.download_asset.side_effect = blocked_download
        batch = cache.begin_batch()
        worker = Thread(target=lambda: batch.download_video_id(client, "burst-id"))
        worker.start()
        assert started.wait(timeout=2)
        finisher = Thread(target=lambda: (batch.finish(), finished.set()))
        finisher.start()
        time.sleep(0.05)
        assert not finished.is_set()
        release.set()
        worker.join(timeout=2)
        finisher.join(timeout=2)
        assert finished.is_set()


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

    def test_download_or_get_never_runs_global_size_eviction(self, cache_dir):
        """Individual downloads leave size maintenance to an explicit batch."""
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
        cache.evict_if_over_limit = MagicMock(wraps=cache.evict_if_over_limit)

        path = cache.download_or_get(client, asset)
        assert path is not None
        assert path.exists()
        assert old_file.exists()
        cache.evict_if_over_limit.assert_not_called()


class TestCachedVideo:
    """Tests for CachedVideo dataclass."""

    def test_cached_video_fields(self):
        """CachedVideo stores path and asset_id."""
        cv = CachedVideo(path=Path("/tmp/test.mp4"), asset_id="abc123")
        assert cv.path == Path("/tmp/test.mp4")
        assert cv.asset_id == "abc123"
