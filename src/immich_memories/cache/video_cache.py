"""File-based video download cache with two-level directory structure."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Container
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from immich_memories.api.immich import ImmichAPIError
from immich_memories.processing.hardware import HWAccelCapabilities
from immich_memories.processing.probe_cache import ProbeCache, ProbeError
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import Asset

logger = logging.getLogger(__name__)

_PARTIAL_SUFFIX = ".part"
_PARTIAL_GRACE_SECONDS = 3600


def _safe_download_error(exc: Exception, client: SyncImmichClient) -> str:
    """Keep credentials out of cache-download diagnostics."""
    message = sanitize_error_message(str(exc))
    api_key = getattr(client, "api_key", None)
    if isinstance(api_key, str) and api_key:
        message = message.replace(api_key, "***")
    return message


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


@lru_cache(maxsize=1)
def _detected_capabilities() -> HWAccelCapabilities:
    from immich_memories.processing.hardware_detection import detect_hardware_acceleration

    return detect_hardware_acceleration()


def analysis_decode_hwaccel_args(codec: str) -> list[str]:
    """Decode-side acceleration for the analysis downscale, empty if unavailable.

    `for_software_filters` matters here: the downscale filters on the CPU, so
    frames have to come back in system memory.
    """
    from immich_memories.processing.hardware import get_ffmpeg_hwaccel_args

    capabilities = _detected_capabilities()
    if not capabilities.has_decoding:
        return []
    return get_ffmpeg_hwaccel_args(
        capabilities,
        operation="decode",
        codec="h265" if codec in ("hevc", "h265") else "h264",
        for_software_filters=True,
    )


def _run_downscale_attempt(
    source: Path,
    dest: Path,
    target_height: int,
    stream_map: str,
    hwaccel_args: list[str],
    timeout: int,
) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        *hwaccel_args,
        "-i",
        str(source),
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
        str(dest),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Downscale attempt failed: %s", exc)
        return False
    if result.returncode == 0 and dest.exists() and dest.stat().st_size > 1024:
        return True
    if hwaccel_args:
        logger.debug("Hardware-accelerated downscale failed: %s", result.stderr[-300:])
    dest.unlink(missing_ok=True)
    return False


def downscale_for_analysis(
    source: Path,
    dest: Path,
    target_height: int,
    *,
    stream_map: str,
    hwaccel_args: list[str] | None = None,
    timeout: int = 120,
) -> bool:
    """Encode the reduced-resolution copy analysis runs on.

    Retries in software when a hardware decode attempt fails. ffmpeg advertising
    a backend is not proof this machine can use it -- a VAAPI node can exist with
    no working driver, a CUDA build with no GPU -- and giving up would push the
    whole analysis onto the full-resolution original, which is the cost the
    acceleration exists to avoid.
    """
    if hwaccel_args and _run_downscale_attempt(
        source, dest, target_height, stream_map, hwaccel_args, timeout
    ):
        return True
    return _run_downscale_attempt(source, dest, target_height, stream_map, [], timeout)


class CacheBatch:
    """One explicit cache-maintenance lifecycle.

    A batch snapshots the cache once at creation, records successful downloads
    in memory, and evicts from that manifest after each download (sparing files
    it already handed out) and once more, unconditionally, when it finishes.
    Call :meth:`invalidate_manifest` only when another actor changed the cache;
    that deliberately permits one replacement scan at finish.
    """

    def __init__(self, cache: VideoDownloadCache) -> None:
        self._cache = cache
        self._condition = threading.Condition(cache._batch_lock)
        self._manifest: dict[Path, _ManifestEntry] = {}
        self._handed_out: set[Path] = set()
        self._invalidated = False
        self._closing = False
        self._finished = False
        self._active_operations = 0

    def _initialize_manifest(self) -> None:
        """Capture the initial manifest before this batch is returned to callers."""
        self._manifest = self._cache._scan_manifest()

    @property
    def finished(self) -> bool:
        """Whether the batch has completed its final size maintenance."""
        with self._condition:
            return self._finished

    @property
    def cache_dir(self) -> Path:
        """Cache root retained for the live-photo merge workspace."""
        return self._cache.cache_dir

    def __enter__(self) -> CacheBatch:
        with self._condition:
            if self._finished:
                raise RuntimeError("Cannot enter a finished cache batch")
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish()

    def invalidate_manifest(self) -> None:
        """Require a replacement scan before finish after an external mutation."""
        with self._condition:
            self._require_open_locked()
            self._invalidated = True

    def download_or_get(self, client: SyncImmichClient, asset: Asset) -> Path | None:
        """Return a cached video or download it, updating only this manifest."""
        return self._run_operation(lambda: self._cache._download_or_get(client, asset, self))

    def download_video_id(
        self, client: SyncImmichClient, video_id: str, extension: str = ".MOV"
    ) -> Path | None:
        """Download a known live-photo component into this batch manifest."""
        return self._run_operation(
            lambda: self._cache._download_id(client, video_id, extension, self)
        )

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
        gpu_decode: bool = True,
    ) -> tuple[Path, Path]:
        """Download and optionally downscale within this batch lifecycle."""
        return self._run_operation(
            lambda: self._cache._get_analysis_video(
                client,
                asset,
                target_height=target_height,
                enable_downscaling=enable_downscaling,
                batch=self,
                gpu_decode=gpu_decode,
            )
        )

    def finish(self) -> int:
        """Evict from the in-memory manifest once; safe to call repeatedly."""
        with self._condition:
            if self._finished:
                return 0
            if self._closing:
                while not self._finished:
                    self._condition.wait()
                return 0
            self._closing = True
            while self._active_operations:
                self._condition.wait()
            invalidated = self._invalidated
        try:
            if invalidated:
                refreshed_manifest = self._cache._scan_manifest()
                with self._condition:
                    self._manifest = refreshed_manifest
            with self._condition:
                manifest = self._manifest.copy()
            return self._cache._evict_manifest(manifest)
        finally:
            # A maintenance failure must never strand the cache in an active
            # state; the original error still propagates to the caller.
            with self._condition:
                self._finished = True
                self._closing = False
                if self._cache._active_batch is self:
                    self._cache._active_batch = None
                self._condition.notify_all()

    def _run_operation(self, operation: Callable[[], Path | None | tuple[Path, Path]]):
        """Keep finish from racing an operation without locking network or FFmpeg I/O."""
        with self._condition:
            self._require_open_locked()
            self._active_operations += 1
        try:
            return operation()
        finally:
            with self._condition:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._condition.notify_all()

    def _record_manifest_entry(self, path: Path) -> None:
        """Update one manifest entry and keep the cap enforced under the batch lock.

        Files this batch already handed to callers are spared until finish: a
        prefetched clip evicted before extraction reads it would fail the run.
        """
        with self._condition:
            self._cache._record_manifest_entry(self._manifest, path)
            self._handed_out.add(path)
            self._cache._evict_manifest(self._manifest, protected=self._handed_out)

    def _require_open_locked(self) -> None:
        if self._finished or self._closing:
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
        self._batch_lock = threading.RLock()
        self._active_batch: CacheBatch | None = None
        self._probe_cache = ProbeCache()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def begin_batch(self) -> CacheBatch:
        """Start an explicit bounded cache lifecycle.

        Batch callers must use the returned object for downloads. The legacy
        one-off methods create a short-lived batch themselves so they continue
        to enforce the size cap instead of allowing unbounded cache growth.
        """
        with self._batch_lock:
            if self._active_batch is not None:
                raise RuntimeError("A cache batch is already active")
            batch = CacheBatch(self)
            self._active_batch = batch
        try:
            batch._initialize_manifest()
        except Exception:
            with self._batch_lock:
                if self._active_batch is batch:
                    self._active_batch = None
            raise
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
        # nor an in-flight/abandoned {asset_id}.ext.part download.
        for match in sub_path.glob(f"{asset_id}.*"):
            if match.suffix == _PARTIAL_SUFFIX:
                continue
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
        batch: CacheBatch,
    ) -> Path | None:
        """Download implementation that updates a caller-owned manifest."""
        # For live photos, the video is a separate asset
        download_id = asset.live_photo_video_id or asset.id
        ext = Path(asset.original_file_name or "video.mp4").suffix or ".mp4"
        if asset.live_photo_video_id:
            ext = ".MOV"  # Live photo videos are always MOV
        return self._download_id(client, download_id, ext, batch)

    def _download_id(
        self,
        client: SyncImmichClient,
        download_id: str,
        extension: str,
        batch: CacheBatch,
    ) -> Path | None:
        """Download one resolved video ID and update the active manifest."""
        cached = self._find_cached(download_id)
        if cached is not None:
            if self._is_readable_media(cached):
                batch._record_manifest_entry(cached)
                return cached
            logger.warning("Evicting cached video ffprobe cannot read: %s", cached)
            cached.unlink(missing_ok=True)
            batch._record_manifest_entry(cached)  # a missing path drops the manifest entry

        dest = self._video_path(download_id, extension)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # WHY: stream into a .part sibling and rename only once complete, so a run
        # killed mid-download can never leave a truncated file at the cached path.
        part = dest.with_name(f"{dest.name}{_PARTIAL_SUFFIX}")

        try:
            client.download_asset(download_id, part)
            if part.exists() and part.stat().st_size > 0:
                os.replace(part, dest)
                batch._record_manifest_entry(dest)
                return dest
            logger.warning("Downloaded file empty or missing: %s", dest)
        except (ImmichAPIError, httpx.HTTPError, OSError, RuntimeError) as e:
            logger.warning(
                "Failed to download video %s: %s", download_id, _safe_download_error(e, client)
            )
        finally:
            part.unlink(missing_ok=True)

        return None

    def _is_readable_media(self, path: Path) -> bool:
        """Treat a cache hit ffprobe rejects as corrupt (truncated by a killed run, bad disk)."""
        try:
            self._probe_cache.get(path)
        except ProbeError:
            return False
        except ValueError:
            # Extension outside the probe whitelist: nothing to verify against.
            pass
        return True

    def get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int = 480,
        enable_downscaling: bool = True,
        gpu_decode: bool = True,
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
                gpu_decode=gpu_decode,
            )

    def _get_analysis_video(
        self,
        client: SyncImmichClient,
        asset: Asset,
        target_height: int,
        enable_downscaling: bool,
        batch: CacheBatch,
        gpu_decode: bool = True,
    ) -> tuple[Path, Path]:
        """Batch-aware implementation for an analysis source video."""
        original = self._download_or_get(client, asset, batch)
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
            from immich_memories.processing.clip_probing import get_main_video_stream_map

            stream_map = get_main_video_stream_map(original)
            hwaccel_args = self._downscale_hwaccel_args(original) if gpu_decode else []
            if downscale_for_analysis(
                original,
                downscaled,
                target_height,
                stream_map=stream_map,
                hwaccel_args=hwaccel_args,
            ):
                batch._record_manifest_entry(downscaled)
                return downscaled, original
            logger.warning("Downscale produced nothing usable for %s, using original", asset.id)
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("Downscaling failed for %s: %s, using original", asset.id, e)
            downscaled.unlink(missing_ok=True)

        return original, original

    def _downscale_hwaccel_args(self, source: Path) -> list[str]:
        try:
            codec = self._probe_cache.get(source).codec
        except (ProbeError, OSError, ValueError):
            return []
        return analysis_decode_hwaccel_args(codec)

    def _scan_manifest(self) -> dict[Path, _ManifestEntry]:
        """Scan once, dropping expired files and abandoned partial downloads."""
        if not self.cache_dir.exists():
            return {}

        now = time.time()
        cutoff = now - (self.max_age_days * 86400)
        manifest: dict[Path, _ManifestEntry] = {}
        for path in self.cache_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.suffix == _PARTIAL_SUFFIX:
                # WHY: a .part nobody wrote to for an hour is a download killed
                # mid-stream; a fresh one may belong to another live cache instance.
                if stat.st_mtime < now - _PARTIAL_GRACE_SECONDS:
                    path.unlink(missing_ok=True)
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

    def _evict_manifest(
        self, manifest: dict[Path, _ManifestEntry], protected: Container[Path] = ()
    ) -> int:
        """Evict oldest unprotected manifest entries without scanning the filesystem again."""
        max_bytes = self.max_size_gb * 1_073_741_824
        # WHY: called after every batch download; skip the per-entry stat while under cap.
        if sum(entry.size for entry in manifest.values()) <= max_bytes:
            return 0
        entries = [entry for entry in manifest.values() if entry.path.exists()]
        total_size = sum(entry.size for entry in entries)
        if total_size <= max_bytes:
            return 0

        count = 0
        for entry in sorted(entries, key=lambda item: item.mtime):
            if total_size <= max_bytes:
                break
            if entry.path in protected:
                continue
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
