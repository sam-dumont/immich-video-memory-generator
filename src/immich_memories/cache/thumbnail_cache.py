"""File-based thumbnail cache keyed by asset ID and size."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from immich_memories.cache.disk_budget import evict_to_budget

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Simple file-based cache for Immich thumbnails."""

    # Scanning the tree on every put would make each thumbnail O(cache size).
    # Checking every so often bounds the overshoot to roughly this many files'
    # worth of data, which at thumbnail sizes is a few MB.
    _PUTS_BETWEEN_BUDGET_CHECKS = 200

    def __init__(self, cache_dir: Path, max_size_mb: float = 500.0) -> None:
        self.cache_dir = cache_dir
        self.max_size_mb = max_size_mb
        self._puts_since_check = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, asset_id: str, size: str) -> Path:
        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        return self.cache_dir / subdir / f"{asset_id}_{size}.jpg"

    def get(self, asset_id: str, size: str) -> bytes | None:
        path = self._path(asset_id, size)
        if path.exists():
            return path.read_bytes()
        return None

    def has(self, asset_id: str, size: str) -> bool:
        return self._path(asset_id, size).exists()

    def get_batch(self, asset_ids: set[str] | list[str], size: str) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for asset_id in asset_ids:
            data = self.get(asset_id, size)
            if data is not None:
                result[asset_id] = data
        return result

    def put(self, asset_id: str, size: str, data: bytes) -> Path:
        path = self._path(asset_id, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._puts_since_check += 1
        if self._puts_since_check >= self._PUTS_BETWEEN_BUDGET_CHECKS:
            self.enforce_budget()
        return path

    def enforce_budget(self) -> int:
        """Drop the least recently used thumbnails until the cache fits."""
        self._puts_since_check = 0
        return evict_to_budget(
            self.cache_dir,
            max_bytes=int(self.max_size_mb * 1_000_000),
            pattern="*.jpg",
        )

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
        max_size_mb = self.max_size_mb
        if not self.cache_dir.exists():
            return {"file_count": 0, "total_size_bytes": 0, "max_size_mb": max_size_mb}

        files = list(self.cache_dir.rglob("*.jpg"))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "file_count": len(files),
            "total_size_bytes": total_size,
            "max_size_mb": max_size_mb,
        }
