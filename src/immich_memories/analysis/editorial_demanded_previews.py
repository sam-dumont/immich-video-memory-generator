"""Fetch a missing preview only when native hashing or picture evidence demands its source."""

from collections.abc import Callable
from io import BytesIO
from time import monotonic

from PIL import Image

from immich_memories.analysis.thumbnail_prefetch import THUMBNAIL_SIZE, cached_preview_bytes
from immich_memories.cache.thumbnail_cache import ThumbnailCache


class DemandedPreviewReader:
    """No whole-pool prefetch or persistent negative cache; one attempt per demanded ID."""

    def __init__(
        self, cache: ThumbnailCache, fetch: Callable[[str], bytes | None], *, allowed_ids: set[str]
    ):
        self._cache, self._fetch, self._allowed = cache, fetch, frozenset(allowed_ids)
        self._seen: dict[str, bytes | None] = {}
        self._metrics = {
            "fetch_attempts": 0,
            "cache_hits": 0,
            "download_bytes": 0,
            "unavailable": 0,
            "fetch_seconds": 0.0,
        }
        self._failures: dict[str, str] = {}

    def __call__(self, asset_id: str) -> bytes | None:
        if asset_id not in self._allowed:
            raise ValueError("picture preview requested outside the conserved card material")
        if asset_id in self._seen:
            return self._seen[asset_id]
        payload = cached_preview_bytes(self._cache, asset_id)
        if payload is not None:
            self._metrics["cache_hits"] += 1
        else:
            self._metrics["fetch_attempts"] += 1
            started = monotonic()
            try:
                payload = self._fetch(asset_id)
                if not payload:
                    raise ValueError("preview unavailable")
                with Image.open(BytesIO(payload)) as image:
                    image.verify()
                self._cache.put(asset_id, THUMBNAIL_SIZE, payload)
                self._metrics["download_bytes"] += len(payload)
            except Exception as exc:  # One missing preview is explicit unavailable evidence.
                payload = None
                self._metrics["unavailable"] += 1
                self._failures[asset_id] = type(exc).__name__
            finally:
                self._metrics["fetch_seconds"] += monotonic() - started
        self._seen[asset_id] = payload
        return payload

    def metrics(self) -> dict:
        return self._metrics | {"failures": self._failures.copy()}
