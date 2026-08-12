"""File-based video download cache with two-level directory structure."""

from __future__ import annotations

import contextlib
import logging
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


@dataclass(frozen=True)
class _ManifestEntry:
    """One cache file captured by a batch's single initial scan."""

    path: Path
    size: int
    mtime: float


class CacheBatch:
    """One explicit cache-maintenance lifecycle.

    A batch snapshots the cache once at creation, records successful downloads
    in memory, and performs size eviction from that manifest when it finishes.
    Call :meth:`invalidate_manifest` only when another actor changed the cache;
    that deliberately permits one replacement scan at finish.
    """

    def __init__(self, cache: VideoDownloadCache) -> None:
        self._cache = cache
        self._manifest = cache._scan_manifest()
        self._invalidated = False
        self._finished = False

    @property
    def finished(self) -> bool:
        """Whether the batch has completed its final size maintenance."""
        return self._finished

    @property
    def cache_dir(self) -> Path:
        """Cache root retained for the live-photo merge workspace."""
        return self._cache.cache_dir

    def __enter__(self) -> CacheBatch:
        if self._finished:
            raise RuntimeError("Cannot enter a finished cache batch")
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish()

    def invalidate_manifest(self) -> None:
        """Require a replacement scan before finish after an external mutation."""
        self._require_open()
        self._invalidated = True

    def download_or_get(self, client: SyncImmichClient, asset: Asset) -> Path | None:
        """Return a cached video or download it, updating only this manifest."""
        self._require_open()
        return self._cache._download_or_get(client, asset, self._manifest)

    def download_video_id(
        self, client: SyncImmichClient, video_id: str, extension: str = ".MOV"
    ) -> Path | None:
        """Download a known live-photo component into this batch manifest."""
        self._require_open()
        return self._cache._download_id(client, video_id, extension, self._manifest)

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
    ) -> tuple[Path, Path]:
        """Download and optionally downscale within this batch lifecycle."""
        self._require_open()
        return self._cache._get_analysis_video(
            client,
            asset,
            target_height=target_height,
            enable_downscaling=enable_downscaling,
            manifest=self._manifest,
        )

    def finish(self) -> int:
        """Evict from the in-memory manifest once; safe to call repeatedly."""
        if self._finished:
            return 0
        try:
            if self._invalidated:
                self._manifest = self._cache._scan_manifest()
            return self._cache._evict_manifest(self._manifest)
        finally:
            # A maintenance failure must never strand the cache in an active
            # state; the original error still propagates to the caller.
            self._finished = True
            self._cache._active_batch = None

    def _require_open(self) -> None:
        if self._finished:
            raise RuntimeError("Cannot use a finished cache batch")


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
        self._active_batch: CacheBatch | None = None
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def begin_batch(self) -> CacheBatch:
        """Start an explicit bounded cache lifecycle.

        Batch callers must use the returned object for downloads. The legacy
        one-off methods create a short-lived batch themselves so they continue
        to enforce the size cap instead of allowing unbounded cache growth.
        """
        if self._active_batch is not None:
            raise RuntimeError("A cache batch is already active")
        batch = CacheBatch(self)
        self._active_batch = batch
        return batch

    def _video_path(self, asset_id: str, ext: str) -> Path:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        return self.cache_dir / subdir / f"{asset_id}{ext}"

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
        # One-off compatibility: each call is safe, while production loops
        # pass an explicit CacheBatch so maintenance occurs once per batch.
        with self.begin_batch() as batch:
            return batch.download_or_get(client, asset)

    def _download_or_get(
        self,
        client: SyncImmichClient,
        asset: Asset,
        manifest: dict[Path, _ManifestEntry],
    ) -> Path | None:
        """Download implementation that updates a caller-owned manifest."""
        # For live photos, the video is a separate asset
        download_id = asset.live_photo_video_id or asset.id
        ext = Path(asset.original_file_name or "video.mp4").suffix or ".mp4"
        if asset.live_photo_video_id:
            ext = ".MOV"  # Live photo videos are always MOV
        return self._download_id(client, download_id, ext, manifest)

    def _download_id(
        self,
        client: SyncImmichClient,
        download_id: str,
        extension: str,
        manifest: dict[Path, _ManifestEntry],
    ) -> Path | None:
        """Download one resolved video ID and update the active manifest."""
        cached = self._find_cached(download_id)
        if cached is not None:
            self._record_manifest_entry(manifest, cached)
            return cached

        dest = self._video_path(download_id, extension)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            client.download_asset(download_id, dest)
            if dest.exists() and dest.stat().st_size > 0:
                self._record_manifest_entry(manifest, dest)
                return dest
            logger.warning("Downloaded file empty or missing: %s", dest)
            dest.unlink(missing_ok=True)
        except (OSError, RuntimeError) as e:
            logger.warning("Failed to download video %s: %s", download_id, e)
            dest.unlink(missing_ok=True)

        return None

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
    ) -> tuple[Path, Path]:
        """Download and optionally create a downscaled copy for analysis.

        Returns:
            Tuple of (analysis_video_path, original_video_path).
            If downscaling is disabled or fails, both are the same path.
        """
        # One-off compatibility mirrors download_or_get: start and finish a
        # bounded lifecycle for callers that do not own a long-running batch.
        with self.begin_batch() as batch:
            return batch.get_analysis_video(
                client,
                asset,
                target_height=target_height,
                enable_downscaling=enable_downscaling,
            )

    def _get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int,
        enable_downscaling: bool,
        manifest: dict[Path, _ManifestEntry],
    ) -> tuple[Path, Path]:
        """Batch-aware implementation for an analysis source video."""
        original = self._download_or_get(client, asset, manifest)
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
                self._record_manifest_entry(manifest, downscaled)
                return downscaled, original
            if downscaled.exists():
                downscaled.unlink()  # Remove corrupted file
                logger.warning("Downscaled file too small/corrupt for %s, using original", asset.id)
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("Downscaling failed for %s: %s, using original", asset.id, e)
            if downscaled.exists():
                downscaled.unlink()

        return original, original

    def _scan_manifest(self) -> dict[Path, _ManifestEntry]:
        """Scan once, removing expired files and returning valid metadata."""
        if not self.cache_dir.exists():
            return {}

        cutoff = time.time() - (self.max_age_days * 86400)
        manifest: dict[Path, _ManifestEntry] = {}
        for path in self.cache_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                path.unlink(missing_ok=True)
                continue
            manifest[path] = _ManifestEntry(path, stat.st_size, stat.st_mtime)
        return manifest

    @staticmethod
    def _record_manifest_entry(manifest: dict[Path, _ManifestEntry], path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            manifest.pop(path, None)
            return
        manifest[path] = _ManifestEntry(path, stat.st_size, stat.st_mtime)

    def _evict_manifest(self, manifest: dict[Path, _ManifestEntry]) -> int:
        """Evict oldest manifest entries without scanning the filesystem again."""
        max_bytes = self.max_size_gb * 1_073_741_824
        entries = [entry for entry in manifest.values() if entry.path.exists()]
        total_size = sum(entry.size for entry in entries)
        if total_size <= max_bytes:
            return 0

        count = 0
        for entry in sorted(entries, key=lambda item: item.mtime):
            if total_size <= max_bytes:
                break
            try:
                entry.path.unlink()
            except OSError:
                continue
            total_size -= entry.size
            manifest.pop(entry.path, None)
            count += 1

        if count:
            logger.info(
                "Cache eviction: removed %d files to stay under %.1f GB", count, self.max_size_gb
            )
        return count

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

        max_bytes = self.max_size_gb * 1_073_741_824  # 1 GB in bytes
        files = [f for f in self.cache_dir.rglob("*") if f.is_file()]
        total_size = sum(f.stat().st_size for f in files)

        if total_size <= max_bytes:
            return 0

        # WHY: evict oldest first (LRU by mtime) to keep recently-used files
        files.sort(key=lambda f: f.stat().st_mtime)
        count = 0
        for f in files:
            if total_size <= max_bytes:
                break
            fsize = f.stat().st_size
            f.unlink(missing_ok=True)
            total_size -= fsize
            count += 1

        if count:
            logger.info(
                "Cache eviction: removed %d files to stay under %.1f GB", count, self.max_size_gb
            )
        return count
