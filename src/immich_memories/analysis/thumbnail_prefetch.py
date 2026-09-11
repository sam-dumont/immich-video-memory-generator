"""Fill the thumbnail cache before phase-1 clustering.

The web UI pre-caches Immich previews in Step 1, so thumbnail de-dup always
had hashes there. ``generate`` and ``auto run`` never populated the cache,
so the same phase hashed nothing and silently kept burst duplicates (#316).
This module fetches whatever previews are missing with the same bounded,
isolated-client worker pattern the video prefetcher uses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = "preview"


def cached_preview_bytes(thumbnail_cache: ThumbnailCache, asset_id: str) -> bytes | None:
    """Read an already-fetched preview without turning atlas construction into a network call."""
    return thumbnail_cache.get(asset_id, THUMBNAIL_SIZE)
