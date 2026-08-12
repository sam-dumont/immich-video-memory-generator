"""Bounded, download-only prefetching for generation media."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Lock
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from immich_memories.api.immich import ImmichAPIError

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import Asset
    from immich_memories.cache.video_cache import CacheBatch


class _DownloadClient(Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """The safe outcome of prefetching one source asset."""

    path: Path | None
    error: str | None = None
    skipped: bool = False


class DownloadCoordinator:
    """Fetch unique source media concurrently with independent sync clients."""

    def __init__(
        self,
        client_factory: Callable[[], _DownloadClient],
        cache_batch: CacheBatch,
        max_workers: int,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._client_factory = client_factory
        self._cache_batch = cache_batch
        self._max_workers = max_workers
        self.max_observed_workers = 0

    def prefetch(
        self,
        assets: Sequence[Asset],
        progress: Callable[[int, int, DownloadResult], None] | None = None,
    ) -> dict[str, DownloadResult]:
        """Download each effective media ID once and return results in input order."""

        def is_static_photo(asset: Asset) -> bool:
            return str(asset.type) == "IMAGE" and not asset.live_photo_video_id

        unique_assets: dict[str, Asset] = {}
        for asset in assets:
            if is_static_photo(asset):
                continue
            unique_assets.setdefault(asset.live_photo_video_id or asset.id, asset)

        work: SimpleQueue[tuple[str, Asset]] = SimpleQueue()
        for item in unique_assets.items():
            work.put(item)

        completed: dict[str, DownloadResult] = {}
        completed_lock = Lock()
        worker_lock = Lock()
        active_workers = 0

        def worker() -> None:
            nonlocal active_workers
            try:
                client = self._client_factory()
            except (ImmichAPIError, OSError, RuntimeError, ValueError, httpx.HTTPError):
                return
            try:
                while True:
                    try:
                        effective_id, asset = work.get_nowait()
                    except Empty:
                        break
                    try:
                        with worker_lock:
                            active_workers += 1
                            self.max_observed_workers = max(
                                self.max_observed_workers, active_workers
                            )
                        try:
                            path = self._cache_batch.download_or_get(
                                cast("SyncImmichClient", client), asset
                            )
                            result = DownloadResult(
                                path=path, error=None if path else "download failed"
                            )
                        finally:
                            with worker_lock:
                                active_workers -= 1
                    except (ImmichAPIError, OSError, RuntimeError, ValueError, httpx.HTTPError):
                        result = DownloadResult(path=None, error="download failed")
                    with completed_lock:
                        completed[effective_id] = result
            finally:
                with contextlib.suppress(Exception):
                    client.close()

        if unique_assets:
            worker_count = min(self._max_workers, len(unique_assets))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(worker) for _ in range(worker_count)]
                for future in futures:
                    future.result()

        for effective_id in unique_assets:
            completed.setdefault(effective_id, DownloadResult(path=None, error="download failed"))

        results: dict[str, DownloadResult] = {}
        ordered_results: list[DownloadResult] = []
        for asset in assets:
            result = (
                DownloadResult(path=None, skipped=True)
                if is_static_photo(asset)
                else completed[asset.live_photo_video_id or asset.id]
            )
            results[asset.id] = result
            ordered_results.append(result)
        if progress is not None:
            for completed_count, result in enumerate(ordered_results, start=1):
                progress(completed_count, len(assets), result)
        return results
