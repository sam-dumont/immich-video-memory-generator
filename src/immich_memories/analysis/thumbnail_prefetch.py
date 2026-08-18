"""Fill the thumbnail cache before phase-1 clustering.

The web UI pre-caches Immich previews in Step 1, so thumbnail de-dup always
had hashes there. ``generate`` and ``auto run`` never populated the cache,
so the same phase hashed nothing and silently kept burst duplicates (#316).
This module fetches whatever previews are missing with the same bounded,
isolated-client worker pattern the video prefetcher uses.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.download_coordinator import (
    DownloadClientFactory,
    DownloadCoordinator,
    DownloadTarget,
    PrefetchAsset,
    build_sync_client_factory,
    has_sync_client_connection,
)
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.compatibility import ApiVersionPolicy
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = "preview"


class ThumbnailPrefetcher:
    """Best-effort preview fetcher; failures degrade de-dup, never abort the run."""

    def __init__(
        self,
        thumbnail_cache: ThumbnailCache,
        client_factory: DownloadClientFactory | None,
        *,
        max_workers: int,
    ) -> None:
        self._thumbnail_cache = thumbnail_cache
        self._client_factory = client_factory
        self._max_workers = max_workers

    @classmethod
    def from_client(
        cls,
        client: SyncImmichClient,
        thumbnail_cache: ThumbnailCache,
        *,
        api_policy: ApiVersionPolicy,
        max_workers: int,
    ) -> ThumbnailPrefetcher:
        """Seed isolated worker clients from the pipeline's client when possible."""
        factory = None
        if has_sync_client_connection(client):
            factory = build_sync_client_factory(client, api_policy)
        return cls(thumbnail_cache, factory, max_workers=max_workers)

    def ensure_cached(self, clips: Sequence[VideoClipInfo]) -> None:
        """Fetch previews that are not cached yet; log (never raise) on failure."""
        missing = [
            clip for clip in clips if not self._thumbnail_cache.has(clip.asset.id, THUMBNAIL_SIZE)
        ]
        if not missing:
            return
        if self._client_factory is None:
            logger.warning(
                "Phase 1: %d clips have no cached thumbnail and the client cannot seed "
                "thumbnail workers; duplicate detection is skipped for them",
                len(missing),
            )
            return

        logger.info(
            "Phase 1: fetching %d missing thumbnails with %d workers",
            len(missing),
            self._max_workers,
        )
        try:
            fetched = self._fetch(missing, self._client_factory)
        except Exception as exc:  # WHY: prefetch is best-effort; the run must continue
            logger.warning(
                "Phase 1: thumbnail prefetch failed (%s); duplicate detection runs on "
                "cached thumbnails only",
                sanitize_error_message(str(exc)),
            )
            return

        failed = len(missing) - fetched
        if failed:
            logger.warning(
                "Phase 1: %d of %d thumbnails could not be fetched; those clips are "
                "excluded from duplicate detection",
                failed,
                len(missing),
            )

    def _fetch(self, clips: Sequence[VideoClipInfo], client_factory: DownloadClientFactory) -> int:
        cache = self._thumbnail_cache

        def fetch_thumbnail(client: SyncImmichClient, asset: PrefetchAsset) -> Path | None:
            data = client.get_asset_thumbnail(asset.id, size=THUMBNAIL_SIZE)
            if not data:
                return None
            return cache.put(asset.id, THUMBNAIL_SIZE, data)

        coordinator = DownloadCoordinator(
            client_factory,
            None,
            self._max_workers,
            download_operation=fetch_thumbnail,
        )
        # Synthetic targets keep the fetch keyed on the clip's own asset ID:
        # live photos need the photo asset's preview, not the video component's.
        targets = [DownloadTarget(id=clip.asset.id) for clip in clips]
        results = coordinator.prefetch(targets)
        for result in results.values():
            if result.error:
                logger.debug("Thumbnail %s: %s", result.asset_id[:8], result.error)
        return sum(1 for result in results.values() if result.path is not None)
