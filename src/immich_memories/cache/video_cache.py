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


class VideoDownloadCache:
    """File-based cache for downloaded Immich videos.

    Uses a two-level directory structure: ``{id[:2]}/{id}{ext}`` to avoid
    too many files in a single directory.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_size_gb: float = 10.0,
        max_age_days: int = 7,
    ) -> None:
        if cache_dir is None:
            from immich_memories.config import get_config

            config = get_config()
            cache_dir = config.cache.video_cache_path
        self.cache_dir = cache_dir
        self.max_size_gb = max_size_gb
        self.max_age_days = max_age_days
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        # For live photos, the video is a separate asset
        download_id = asset.live_photo_video_id or asset.id
        cached = self._find_cached(download_id)
        if cached is not None:
            return cached

        ext = Path(asset.original_file_name or "video.mp4").suffix or ".mp4"
        if asset.live_photo_video_id:
            ext = ".MOV"  # Live photo videos are always MOV
        dest = self._video_path(download_id, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            client.download_asset(download_id, dest)
            if dest.exists() and dest.stat().st_size > 0:
                return dest
            logger.warning("Downloaded file empty or missing: %s", dest)
            dest.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to download video %s", download_id, exc_info=True)
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
        original = self.download_or_get(client, asset)
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
        except Exception:
            logger.debug("Downscaling failed for %s, using original", asset.id)
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
