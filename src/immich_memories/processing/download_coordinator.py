"""Bounded, download-only prefetching with one synchronous client per worker."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeGuard

import httpx

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.api.immich import ImmichAPIError, SyncImmichClient
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.cache.video_cache import CacheBatch


logger = logging.getLogger(__name__)


class PrefetchAsset(Protocol):
    """The small asset shape needed for download coordination."""

    @property
    def id(self) -> str: ...

    @property
    def live_photo_video_id(self) -> str | None: ...

    @property
    def type(self) -> object: ...


class SyncClientConnection(Protocol):
    """Connection metadata required to clone an isolated download client."""

    @property
    def base_url(self) -> str: ...

    @property
    def api_key(self) -> str: ...

    @property
    def timeout(self) -> float: ...


@dataclass(frozen=True)
class DownloadTarget:
    """Minimal synthetic asset for a live-photo burst component download."""

    id: str
    original_file_name: str = "video.MOV"
    live_photo_video_id: str | None = None
    type: str = "VIDEO"


@dataclass(frozen=True)
class DownloadResult:
    """One input asset's prefetched local source or isolated failure."""

    asset_id: str
    download_id: str | None
    path: Path | None
    error: str | None = None
    skipped: bool = False


DownloadClientFactory = Callable[[], SyncImmichClient]
DownloadOperation = Callable[[SyncImmichClient, PrefetchAsset], Path | None]
ProgressCallback = Callable[[int, int, DownloadResult], None]


def has_sync_client_connection(client: object) -> TypeGuard[SyncClientConnection]:
    """Return whether a caller-owned client can seed isolated workers."""
    return all(hasattr(client, name) for name in ("base_url", "api_key", "timeout"))


def build_sync_client_factory(
    source_client: SyncClientConnection,
    api_policy: ApiVersionPolicy,
) -> DownloadClientFactory:
    """Capture connection settings for isolated worker clients.

    The factory deliberately does no I/O. In particular, AUTO stays AUTO so
    static-photo-only and local-file-only runs do not probe the server.
    """
    base_url = source_client.base_url
    api_key = source_client.api_key
    timeout = source_client.timeout

    def make_client() -> SyncImmichClient:
        return SyncImmichClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            api_version=api_policy,
        )

    return make_client


class DownloadCoordinator:
    """Prefetch unique video IDs with bounded isolated synchronous clients.

    Every worker constructs and closes its own client in the same thread. The
    supplied cache batch is only used for file download/cache writes; FFmpeg
    extraction remains in the caller's sequential phase.
    """

    def __init__(
        self,
        client_factory: DownloadClientFactory,
        cache_batch: CacheBatch | None,
        max_workers: int,
        *,
        download_operation: DownloadOperation | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        self._client_factory = client_factory
        self._cache_batch = cache_batch
        self._max_workers = max_workers
        self._download_operation = download_operation
        self._worker_lock = threading.Lock()
        self._active_workers = 0
        self.max_observed_workers = 0

    def prefetch(
        self,
        assets: Iterable[PrefetchAsset],
        progress: ProgressCallback | None = None,
    ) -> dict[str, DownloadResult]:
        """Download unique video/live-photo IDs and return input-order results."""
        ordered_assets = list(assets)
        unique_assets: dict[str, PrefetchAsset] = {}
        for asset in ordered_assets:
            if self._is_static_photo(asset):
                continue
            unique_assets.setdefault(self._download_id(asset), asset)

        downloaded = self._download_unique(unique_assets)
        results: dict[str, DownloadResult] = {}
        total = len(ordered_assets)
        for index, asset in enumerate(ordered_assets, start=1):
            if self._is_static_photo(asset):
                result = DownloadResult(asset.id, None, None, skipped=True)
            else:
                resolved_id = self._download_id(asset)
                shared = downloaded[resolved_id]
                result = DownloadResult(asset.id, resolved_id, shared.path, shared.error)
            results[asset.id] = result
            if progress is not None:
                progress(index, total, result)
        return results

    def _download_unique(
        self, unique_assets: dict[str, PrefetchAsset]
    ) -> dict[str, DownloadResult]:
        if not unique_assets:
            return {}
        worker_count = min(self._max_workers, len(unique_assets))
        batches: list[list[tuple[str, PrefetchAsset]]] = [[] for _ in range(worker_count)]
        for index, item in enumerate(unique_assets.items()):
            batches[index % worker_count].append(item)

        results: dict[str, DownloadResult] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(self._run_worker, batch) for batch in batches]
            for future in futures:
                results.update(future.result())
        return results

    def _run_worker(self, assets: list[tuple[str, PrefetchAsset]]) -> dict[str, DownloadResult]:
        with self._worker_lock:
            self._active_workers += 1
            self.max_observed_workers = max(self.max_observed_workers, self._active_workers)
        try:
            try:
                client = self._client_factory()
            except Exception as exc:
                safe_error = _safe_error_message(exc)
                return {
                    download_id: DownloadResult(asset.id, download_id, None, safe_error)
                    for download_id, asset in assets
                }

            try:
                return {
                    download_id: self._download_one(client, asset, download_id)
                    for download_id, asset in assets
                }
            finally:
                try:
                    client.close()
                except Exception as exc:
                    logger.warning(
                        "Failed to close isolated download client: %s",
                        _safe_error_message(exc, client),
                    )
        finally:
            with self._worker_lock:
                self._active_workers -= 1

    def _download_one(
        self, client: SyncImmichClient, asset: PrefetchAsset, download_id: str
    ) -> DownloadResult:
        try:
            operation = self._download_operation or self._download_with_batch
            path = operation(client, asset)
            if path is None:
                return DownloadResult(asset.id, download_id, None, "download returned no file")
            return DownloadResult(asset.id, download_id, path)
        except (ImmichAPIError, httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            return DownloadResult(asset.id, download_id, None, _safe_error_message(exc, client))

    def _download_with_batch(self, client: SyncImmichClient, asset: PrefetchAsset) -> Path | None:
        if self._cache_batch is None:
            raise RuntimeError("A download operation is required when video caching is disabled")
        return self._cache_batch.download_or_get(client, asset)  # type: ignore[arg-type]

    @staticmethod
    def _download_id(asset: PrefetchAsset) -> str:
        return asset.live_photo_video_id or asset.id

    @staticmethod
    def _is_static_photo(asset: PrefetchAsset) -> bool:
        return getattr(asset.type, "value", asset.type) == "IMAGE" and not asset.live_photo_video_id


def _safe_error_message(exc: Exception, client: SyncImmichClient | None = None) -> str:
    """Return a coordinator diagnostic without credentials from a worker client."""
    message = sanitize_error_message(str(exc))
    api_key = getattr(client, "api_key", None)
    if isinstance(api_key, str) and api_key:
        message = message.replace(api_key, "***")
    return message
