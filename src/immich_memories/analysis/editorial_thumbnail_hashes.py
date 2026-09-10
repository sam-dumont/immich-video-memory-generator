"""Reuse the exact duplicate hash without decoding unchanged preview bytes again."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash
from immich_memories.cache.sqlite_conn import ThreadOwnedConnections

METHOD = f"thumbnail-ahash-v1-bgr-gray-area-opencv-{cv2.__version__}-numpy-{np.__version__}"
_SCHEMA = """CREATE TABLE IF NOT EXISTS thumbnail_hashes (
    source_key TEXT PRIMARY KEY, thumbnail_hash TEXT NOT NULL
)"""


class CachedThumbnailHasher:
    """A preview's contents, hash size and implementation identify its reusable fact."""

    def __init__(
        self,
        cache_path: Path,
        read_preview: Callable[[str], bytes | None],
        *,
        hash_size: int = 8,
    ) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.touch(mode=0o600, exist_ok=True)
        cache_path.chmod(0o600)
        self._connections = ThreadOwnedConnections(cache_path, _SCHEMA)
        self._read_preview = read_preview
        self._hash_size = hash_size
        self._method = METHOD
        self._counts: dict[str, int] = {
            "requests": 0,
            "cache_hits": 0,
            "hashes_computed": 0,
            "unavailable": 0,
            "preview_bytes": 0,
        }
        self._wall_seconds = 0.0

    def __call__(self, asset_id: str) -> str | None:
        started = time.monotonic()
        self._counts["requests"] += 1
        try:
            payload = self._read_preview(asset_id)
            if not payload:
                self._counts["unavailable"] += 1
                return None
            self._counts["preview_bytes"] += len(payload)
            key = hashlib.sha256(f"{METHOD}\0{self._hash_size}\0".encode() + payload).hexdigest()
            with self._connections.connection() as connection:
                row = connection.execute(
                    "SELECT thumbnail_hash FROM thumbnail_hashes WHERE source_key=?", (key,)
                ).fetchone()
                if row is not None:
                    self._counts["cache_hits"] += 1
                    return str(row[0])
                value = compute_thumbnail_hash(payload, self._hash_size)
                self._counts["hashes_computed"] += 1
                if not value:
                    self._counts["unavailable"] += 1
                    return None
                connection.execute(
                    "INSERT OR REPLACE INTO thumbnail_hashes VALUES (?, ?)", (key, value)
                )
                connection.commit()
                return value
        finally:
            self._wall_seconds += time.monotonic() - started

    def metrics(self) -> dict:
        reported: dict[str, object] = {"method": self._method, "hash_size": self._hash_size}
        reported.update(self._counts)
        reported["wall_seconds"] = round(self._wall_seconds, 3)
        return reported

    def close(self) -> None:
        self._connections.close()
