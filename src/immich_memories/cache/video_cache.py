"""File-based video download cache with two-level directory structure."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import Asset

logger = logging.getLogger(__name__)


@dataclass
class CachedVideo:
    """Metadata for a cached video file."""

    path: Path
    asset_id: str


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """Manifest identity used for size eviction and external-mutation detection."""

    size: int
    mtime_ns: int


def _current_entry(path: Path) -> _CacheEntry:
    stat = path.stat()
    return _CacheEntry(stat.st_size, stat.st_mtime_ns)


class CacheBatch:
    """One download batch with a single cache-maintenance lifecycle."""

    def __init__(self, cache: VideoDownloadCache) -> None:
        self._cache = cache
        self._manifest, self._directory_mtimes = cache._scan_manifest()
        self._finished = False
        self._finishing = False
        self._active_operations = 0
        self._active_by_thread: dict[int, int] = {}
        self._condition = threading.Condition(threading.RLock())

    def __enter__(self) -> CacheBatch:
        return self

    @property
    def cache_dir(self) -> Path:
        """Expose the owned cache root for legacy live-burst download helpers."""
        return self._cache.cache_dir

    def __exit__(self, _exc_type: object, exc: object, _traceback: object) -> None:
        try:
            self.finish()
        except OSError:
            if exc is None:
                raise
            logger.warning("Cache batch cleanup failed after an item error")

    def _record(self, path: Path, *, touch: bool = False) -> None:
        if touch:
            with contextlib.suppress(OSError):
                path.touch()
        stat = path.stat()
        self._manifest[path] = _CacheEntry(stat.st_size, stat.st_mtime_ns)
        self._refresh_directory_mtimes(path.parent)

    def _refresh_directory_mtimes(self, directory: Path) -> None:
        current = directory
        while current.is_relative_to(self._cache.cache_dir):
            self._directory_mtimes[current] = current.stat().st_mtime_ns
            if current == self._cache.cache_dir:
                break
            current = current.parent

    def _begin_operation(self) -> None:
        """Reserve one operation before its network body starts."""
        with self._condition:
            thread_id = threading.get_ident()
            reentrant = self._active_by_thread.get(thread_id, 0) > 0
            if self._finished or (self._finishing and not reentrant):
                raise RuntimeError("Cache batch is closed")
            self._active_operations += 1
            self._active_by_thread[thread_id] = self._active_by_thread.get(thread_id, 0) + 1

    def _end_operation(self) -> None:
        with self._condition:
            self._active_operations -= 1
            thread_id = threading.get_ident()
            remaining = self._active_by_thread[thread_id] - 1
            if remaining:
                self._active_by_thread[thread_id] = remaining
            else:
                del self._active_by_thread[thread_id]
            self._condition.notify_all()

    def record_path(self, path: Path, *, touch: bool = True) -> Path:
        """Record one deterministic cache-owned path without exposing a tree operation."""
        self._begin_operation()
        try:
            with self._condition:
                cache_root = self._cache.cache_dir.resolve()
                resolved = path.resolve()
                if not resolved.is_relative_to(cache_root):
                    raise ValueError("Cache batch can only record paths inside its cache root")
                if not path.is_file() or path.stat().st_size <= 0:
                    raise ValueError("Cache batch can only record a non-empty file")
                self._record(path, touch=touch)
                return path
        finally:
            self._end_operation()

    def record_absence(self, path: Path) -> None:
        """Record one deterministic failed-download path after controlled cleanup."""
        self._begin_operation()
        try:
            with self._condition:
                cache_root = self._cache.cache_dir.resolve()
                resolved = path.resolve()
                if not resolved.is_relative_to(cache_root):
                    raise ValueError("Cache batch can only record paths inside its cache root")
                if path.exists():
                    raise ValueError("Cache batch can only record an absent path")
                self._manifest.pop(path, None)
                self._refresh_directory_mtimes(path.parent)
        finally:
            self._end_operation()

    def download_or_get(self, client: SyncImmichClient, asset: Asset) -> Path | None:
        """Return one cached/downloaded asset and update this batch's manifest."""
        self._begin_operation()
        try:
            destination = self._cache._download_path(asset)
            try:
                path = self._cache.download_or_get(client, asset)
            except BaseException:
                destination.unlink(missing_ok=True)
                with self._condition:
                    self._manifest.pop(destination, None)
                    self._refresh_directory_mtimes(destination.parent)
                raise
            with self._condition:
                if path is not None:
                    self._record(path, touch=True)
                else:
                    self._manifest.pop(destination, None)
                    self._refresh_directory_mtimes(destination.parent)
            return path
        finally:
            self._end_operation()

    def download_video_id(self, client: SyncImmichClient, video_id: str) -> Path | None:
        """Download one deterministic live-photo component within this batch admission."""
        self._begin_operation()
        destination = self._cache._video_path(video_id, ".MOV")
        try:
            cached = self._cache._find_cached(video_id)
            if cached is not None:
                with self._condition:
                    self._record(cached, touch=True)
                return cached

            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                client.download_asset(video_id, destination)
            except (OSError, RuntimeError):
                destination.unlink(missing_ok=True)
                with self._condition:
                    self._manifest.pop(destination, None)
                    self._refresh_directory_mtimes(destination.parent)
                return None
            except BaseException:
                destination.unlink(missing_ok=True)
                with self._condition:
                    self._manifest.pop(destination, None)
                    self._refresh_directory_mtimes(destination.parent)
                raise

            if destination.exists() and destination.stat().st_size > 0:
                with self._condition:
                    self._record(destination)
                return destination
            destination.unlink(missing_ok=True)
            with self._condition:
                self._manifest.pop(destination, None)
                self._refresh_directory_mtimes(destination.parent)
            return None
        finally:
            self._end_operation()

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
    ) -> tuple[Path, Path]:
        """Return analysis media while keeping generated cache files in the manifest."""
        self._begin_operation()
        try:
            result = self._cache.get_analysis_video(
                client,
                asset,
                target_height=target_height,
                enable_downscaling=enable_downscaling,
                batch=self,
            )
            analysis_video, original_video = result
            with self._condition:
                self._record(original_video)
                if analysis_video != original_video:
                    self._record(analysis_video, touch=True)
            return result
        finally:
            self._end_operation()

    def finish(self) -> int:
        """Evict from the manifest once; rescan only after external mutation."""
        with self._condition:
            thread_id = threading.get_ident()
            if self._active_by_thread.get(thread_id):
                raise RuntimeError("Cannot finish a cache batch from an active operation")
            if self._finished:
                return 0
            if self._finishing:
                while self._finishing:
                    self._condition.wait()
                return 0
            self._finishing = True
            while self._active_operations:
                self._condition.wait()
            try:
                if not self._cache._manifest_matches(self._manifest, self._directory_mtimes):
                    self._manifest, self._directory_mtimes = self._cache._scan_manifest()
                return self._cache._evict_manifest(self._manifest)
            finally:
                self._finished = True
                self._finishing = False
                self._condition.notify_all()


class VideoDownloadCache:
    """File-based cache for downloaded Immich videos.

    Uses a two-level directory structure: ``{id[:2]}/{id}{ext}`` to avoid
    too many files in a single directory.
    """

    def __init__(
        self,
        cache_dir: Path,
        max_size_gb: float = 10.0,
        max_age_days: int = 7,
    ) -> None:
        self.cache_dir = cache_dir
        self.max_size_gb = max_size_gb
        self.max_age_days = max_age_days
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def begin_batch(self) -> CacheBatch:
        """Start one explicit expiry, download, and size-eviction lifecycle."""
        return CacheBatch(self)

    def _scan_manifest(
        self, *, remove_expired: bool = True
    ) -> tuple[dict[Path, _CacheEntry], dict[Path, int]]:
        """Remove expired files and snapshot current file/directory identity once."""
        cutoff = time.time() - (self.max_age_days * 86400)
        manifest: dict[Path, _CacheEntry] = {}
        found_directories = {self.cache_dir}
        for path in self.cache_dir.rglob("*"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if path.is_dir():
                found_directories.add(path)
                continue
            if not path.is_file():
                continue
            if remove_expired and stat.st_mtime < cutoff:
                path.unlink(missing_ok=True)
                continue
            manifest[path] = _CacheEntry(stat.st_size, stat.st_mtime_ns)
        directories = {
            directory: directory.stat().st_mtime_ns
            for directory in found_directories
            if directory.exists()
        }
        return manifest, directories

    @staticmethod
    def _manifest_matches(
        manifest: dict[Path, _CacheEntry],
        directory_mtimes: dict[Path, int],
    ) -> bool:
        """Check known paths without walking the tree again."""
        try:
            files_match = all(_current_entry(path) == entry for path, entry in manifest.items())
            directories_match = all(
                path.stat().st_mtime_ns == mtime_ns for path, mtime_ns in directory_mtimes.items()
            )
        except FileNotFoundError:
            return False
        return files_match and directories_match

    def _evict_manifest(self, manifest: dict[Path, _CacheEntry]) -> int:
        """Remove oldest manifest entries until the configured size limit is met."""
        max_bytes = self.max_size_gb * 1_073_741_824
        total_size = sum(entry.size for entry in manifest.values())
        count = 0
        for path, entry in sorted(manifest.items(), key=lambda item: item[1].mtime_ns):
            if total_size <= max_bytes:
                break
            path.unlink(missing_ok=True)
            total_size -= entry.size
            count += 1
        if count:
            logger.info(
                "Cache eviction: removed %d files to stay under %.1f GB",
                count,
                self.max_size_gb,
            )
        return count

    def _video_path(self, asset_id: str, ext: str) -> Path:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        return self.cache_dir / subdir / f"{asset_id}{ext}"

    def _download_path(self, asset: Asset) -> Path:
        """Return the deterministic cache destination for one Immich asset."""
        download_id = asset.live_photo_video_id or asset.id
        ext = Path(asset.original_file_name or "video.mp4").suffix or ".mp4"
        if asset.live_photo_video_id:
            ext = ".MOV"
        return self._video_path(download_id, ext)

    def _find_cached(self, asset_id: str) -> Path | None:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        sub_path = self.cache_dir / subdir
        if not sub_path.exists():
            return None
        # Match {asset_id}.* but not {asset_id}_480p.* (analysis downscale)
        for match in sub_path.glob(f"{asset_id}.*"):
            if match.is_file() and match.stat().st_size > 0:
                return match
        return None

    def download_or_get(
        self,
        client: SyncImmichClient,
        asset: Asset,
    ) -> Path | None:
        """Return cached video path, downloading if needed.

        For Live Photos, downloads the video component (live_photo_video_id)
        instead of the IMAGE asset.
        """
        # For live photos, the video is a separate asset
        download_id = asset.live_photo_video_id or asset.id
        cached = self._find_cached(download_id)
        if cached is not None:
            return cached

        dest = self._download_path(asset)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            client.download_asset(download_id, dest)
            if dest.exists() and dest.stat().st_size > 0:
                return dest
            logger.warning("Downloaded file empty or missing: %s", dest)
            dest.unlink(missing_ok=True)
        except (OSError, RuntimeError):
            logger.warning("Video download failed for asset %s", download_id)
            dest.unlink(missing_ok=True)

        return None

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
        *,
        batch: CacheBatch | None = None,
    ) -> tuple[Path, Path]:
        """Download and optionally create a downscaled copy for analysis.

        Returns:
            Tuple of (analysis_video_path, original_video_path).
            If downscaling is disabled or fails, both are the same path.
        """
        original = (
            batch.download_or_get(client, asset)
            if batch is not None
            else self.download_or_get(client, asset)
        )
        if original is None:
            msg = f"Failed to download video {asset.id}"
            raise ValueError(msg)

        if not enable_downscaling:
            return original, original

        # Check for existing downscaled version (use video ID for live photos)
        video_id = asset.live_photo_video_id or asset.id
        subdir = video_id[:2] if len(video_id) >= 2 else "00"
        sub_path = self.cache_dir / subdir
        downscaled_matches = list(sub_path.glob(f"{video_id}_480p.*"))
        if downscaled_matches and downscaled_matches[0].stat().st_size > 1024:
            return downscaled_matches[0], original

        # Try to create downscaled version
        downscaled = sub_path / f"{video_id}_480p{original.suffix}"
        try:
            import subprocess

            from immich_memories.processing.clip_probing import get_main_video_stream_map

            stream_map = get_main_video_stream_map(original)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(original),
                "-map",
                stream_map,
                "-vf",
                f"scale=-2:{target_height}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-movflags",
                "+faststart",
                "-an",
                str(downscaled),
            ]
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and downscaled.exists() and downscaled.stat().st_size > 1024:
                return downscaled, original
            if downscaled.exists():
                downscaled.unlink()  # Remove corrupted file
                logger.warning("Downscaled file too small/corrupt for %s, using original", asset.id)
        except (OSError, subprocess.SubprocessError):
            logger.debug("Downscaling failed for asset %s; using original", asset.id)
            if downscaled.exists():
                downscaled.unlink()

        return original, original

    def get_stats(self) -> dict:
        if not self.cache_dir.exists():
            return {
                "file_count": 0,
                "total_size_bytes": 0,
                "max_size_gb": self.max_size_gb,
            }

        files = [f for f in self.cache_dir.rglob("*") if f.is_file()]
        total_size = sum(f.stat().st_size for f in files)
        return {
            "file_count": len(files),
            "total_size_bytes": total_size,
            "max_size_gb": self.max_size_gb,
        }

    def clear(self) -> int:
        """Remove all cached videos. Returns count of removed files."""
        count = 0
        if not self.cache_dir.exists():
            return 0

        for f in self.cache_dir.rglob("*"):
            if f.is_file():
                f.unlink(missing_ok=True)
                count += 1

        # Clean empty subdirectories
        for d in sorted(self.cache_dir.rglob("*"), reverse=True):
            if d.is_dir():
                with contextlib.suppress(OSError):
                    d.rmdir()

        return count

    def evict_old(self) -> int:
        """Remove files older than max_age_days. Returns count removed."""
        if not self.cache_dir.exists():
            return 0

        cutoff = time.time() - (self.max_age_days * 86400)
        count = 0
        for f in self.cache_dir.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                count += 1
        return count

    def evict_if_over_limit(self) -> int:
        """Remove oldest files until cache is under max_size_gb. Returns count removed."""
        if not self.cache_dir.exists():
            return 0

        manifest, _ = self._scan_manifest(remove_expired=False)
        return self._evict_manifest(manifest)
