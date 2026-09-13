"""One HTTP route for every thumbnail the web UI shows.

Until this route existed, each picture in a grid was a base64 data URI of
Immich's *preview*: roughly half a megabyte of text in a DOM attribute and six
megabytes of decoded bitmap per cell, kept alive for as long as the cell was.
Two hundred cells was over a gigabyte, and the tab died (#824).

The route serves bytes from the session's thumbnail cache, so the browser does
the caching, lazy decoding and eviction it is built for. It is one
parameterised route, never one route per asset (`tests/test_ui_media_routes.py`
explains why), and it sits behind the same auth middleware as every page
because it is not a bypass path.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from typing import Any

from starlette.responses import Response

from immich_memories.cache.thumbnail_cache import ThumbnailCache

THUMB_PATH = "/media/thumb/{asset_id}"

# Grid cells are 140 px to 320 px wide; a 320 px long side covers a 2x display.
GRID_THUMBNAIL_PX = 320

_ASSET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SIZES = frozenset({"thumbnail", "preview"})
_CACHE_CONTROL = "private, max-age=86400"

CacheResolver = Callable[[], ThumbnailCache | None]


def thumbnail_url(asset_id: str, *, size: str = "thumbnail") -> str:
    """The URL a page puts in an <img> for this asset at this size."""
    path = THUMB_PATH.format(asset_id=asset_id)
    return path if size == "thumbnail" else f"{path}?size={size}"


def downscale_to_thumbnail(payload: bytes, *, px: int = GRID_THUMBNAIL_PX) -> bytes | None:
    """A cached preview re-encoded to grid size, or None if it does not decode."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            image = opened.convert("RGB")
            image.thumbnail((px, px))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=80)
    except Exception:  # WHY: a corrupt preview must degrade to a placeholder, not a 500
        return None
    return buffer.getvalue()


def load_thumbnail(cache: ThumbnailCache, asset_id: str, size: str) -> bytes | None:
    """Bytes for the asset at the size, deriving the grid thumbnail from the preview once.

    The analysis pipeline fills the cache with previews, so the first request
    for a thumbnail pays one downscale and stores it; every later request is a
    file read.
    """
    if size == "preview":
        return cache.get(asset_id, "preview")
    cached = cache.get(asset_id, "thumbnail")
    if cached:
        return cached
    preview = cache.get(asset_id, "preview")
    if not preview:
        return None
    small = downscale_to_thumbnail(preview)
    if small:
        cache.put(asset_id, "thumbnail", small)
    return small


def register_media_route(target: Any, resolve_cache: CacheResolver) -> None:
    """Mount the thumbnail route on a FastAPI app (or the NiceGUI app)."""

    async def thumbnail(asset_id: str, size: str = "thumbnail") -> Response:
        if not _ASSET_ID.match(asset_id) or size not in _SIZES:
            return Response(status_code=404)
        cache = resolve_cache()
        if cache is None:
            return Response(status_code=404)
        data = load_thumbnail(cache, asset_id, size)
        if data is None:
            return Response(status_code=404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": _CACHE_CONTROL})

    target.add_api_route(THUMB_PATH, thumbnail, methods=["GET"])
