"""Step 2 re-hashed every thumbnail on every render.

`_detect_duplicates` runs on each `/step2` build, and `/step2` is re-navigated on
many interactions. Each clip costs a `read_bytes` plus a `cv2.imdecode` of the
1440px preview -- measured at 7.1 ms on real thumbnails, so 500 clips is 3.6s of
work per render, on the event loop, recomputing values that cannot have changed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.thumbnail_clustering import _compute_thumbnail_hashes


class _CountingCache:
    """A thumbnail cache that reports how often it was actually read."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads
        self.reads: list[str] = []

    def get(self, asset_id: str, size: str) -> bytes | None:  # noqa: ARG002
        self.reads.append(asset_id)
        return self._payloads.get(asset_id)


def _clip(asset_id: str):
    clip = MagicMock()
    clip.asset.id = asset_id
    return clip


@pytest.fixture
def jpeg_bytes() -> bytes:
    import cv2
    import numpy as np

    image = np.zeros((64, 64, 3), dtype=np.uint8)
    image[:32] = 255
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def test_a_known_hash_is_not_recomputed(jpeg_bytes: bytes):
    clips = [_clip("a"), _clip("b")]
    cache = _CountingCache({"a": jpeg_bytes, "b": jpeg_bytes})

    first = _compute_thumbnail_hashes(clips, cache)
    cache.reads.clear()
    second = _compute_thumbnail_hashes(clips, cache, known=first)

    assert second == first
    assert cache.reads == [], "thumbnails were re-read despite known hashes"


def test_only_the_new_clips_are_hashed(jpeg_bytes: bytes):
    """Adding a clip must not re-hash the ones already seen."""
    cache = _CountingCache({"a": jpeg_bytes, "b": jpeg_bytes})
    known = _compute_thumbnail_hashes([_clip("a")], cache)
    cache.reads.clear()

    _compute_thumbnail_hashes([_clip("a"), _clip("b")], cache, known=known)

    assert cache.reads == ["b"]


def test_without_known_hashes_it_behaves_as_before(jpeg_bytes: bytes):
    cache = _CountingCache({"a": jpeg_bytes})

    hashes = _compute_thumbnail_hashes([_clip("a")], cache)

    assert set(hashes) == {"a"}
    assert cache.reads == ["a"]


def test_a_clip_with_no_thumbnail_is_not_cached_as_missing(jpeg_bytes: bytes):
    """A thumbnail that arrives later must still get hashed."""
    cache = _CountingCache({})
    known = _compute_thumbnail_hashes([_clip("a")], cache)
    cache._payloads["a"] = jpeg_bytes
    cache.reads.clear()

    hashes = _compute_thumbnail_hashes([_clip("a")], cache, known=known)

    assert "a" in hashes
