"""File-based thumbnail cache keyed by asset ID and size."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Simple file-based cache for Immich thumbnails."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, asset_id: str, size: str) -> Path:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        return self.cache_dir / subdir / f"{asset_id}_{size}.jpg"

    def get(self, asset_id: str, size: str) -> bytes | None:
        path = self._path(asset_id, size)
        if path.exists():
            return path.read_bytes()
        return None

    def get_batch(self, asset_ids: set[str] | list[str], size: str) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for asset_id in asset_ids:
            data = self.get(asset_id, size)
            if data is not None:
                result[asset_id] = data
        return result

    def put(self, asset_id: str, size: str, data: bytes) -> None:
        path = self._path(asset_id, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def clear(self) -> int:
        """Remove all cached thumbnails. Returns count of removed files."""

        count = 0
        if self.cache_dir.exists():
            for f in self.cache_dir.rglob("*.jpg"):
                f.unlink(missing_ok=True)
                count += 1
            # Clean empty subdirectories
            for d in sorted(self.cache_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    with contextlib.suppress(OSError):
                        d.rmdir()
        return count

    def get_stats(self) -> dict:
        max_size_mb = 500.0  # Default max thumbnail cache size
        if not self.cache_dir.exists():
            return {"file_count": 0, "total_size_bytes": 0, "max_size_mb": max_size_mb}

        files = list(self.cache_dir.rglob("*.jpg"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "file_count": len(files),
            "total_size_bytes": total_size,
            "max_size_mb": max_size_mb,
        }
