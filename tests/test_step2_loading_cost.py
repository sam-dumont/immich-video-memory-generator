"""Step 2 did redundant work before it fetched anything.

Two costs measured on a real cache, neither of them the network:

* The set of already-cached thumbnails was built with `get_batch(...).keys()`,
  which reads every cached thumbnail's *bytes* off disk to answer a question
  about which ids exist. On 1583 real thumbnails that is 543 MB read and thrown
  away, 244 ms against 7 ms -- and at 5000 clips, ~1.7 GB.
* Each batch of ten built its own `SyncImmichClient`, so 500 thumbnails meant 50
  HTTP clients, each with its own connection pool and private event loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.cache.thumbnail_cache import ThumbnailCache


async def _call_directly(func, *args, **kwargs):
    """Stand in for nicegui.run.io_bound without a worker pool."""
    return func(*args, **kwargs)


class TestFindingCachedThumbnails:
    @staticmethod
    def _cache_with(tmp_path: Path, ids: list[str]) -> ThumbnailCache:
        cache = ThumbnailCache(cache_dir=tmp_path / "thumbnails")
        for asset_id in ids:
            cache.put(asset_id, "preview", b"x" * 4096)
        return cache

    def test_it_reports_which_ids_are_cached(self, tmp_path: Path):
        cache = self._cache_with(tmp_path, ["a", "b"])

        assert cache.cached_ids(["a", "b", "c"], "preview") == {"a", "b"}

    def test_it_does_not_read_the_thumbnails(self, tmp_path: Path, monkeypatch):
        """Reading bytes to answer an existence question is the whole bug."""
        cache = self._cache_with(tmp_path, ["a", "b"])

        def fail_on_read(*_args, **_kwargs):
            raise AssertionError("thumbnail bytes were read to build an id set")

        monkeypatch.setattr(Path, "read_bytes", fail_on_read)

        assert cache.cached_ids(["a", "b", "c"], "preview") == {"a", "b"}

    def test_an_empty_request_is_not_an_error(self, tmp_path: Path):
        assert self._cache_with(tmp_path, []).cached_ids([], "preview") == set()


class TestFetchingThumbnails:
    """Each batch of ten built its own client, so 500 thumbnails meant 50 of
    them -- each an httpx connection pool and a private event loop, discarded
    after ten requests, and none of them reusing a connection.
    """

    @pytest.mark.asyncio
    async def test_one_client_serves_the_whole_fetch(self, monkeypatch):
        from unittest.mock import MagicMock

        from immich_memories.ui.pages import step2_loading

        built = []

        class _Client:
            def __init__(self, **_kwargs):
                built.append(1)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def get_asset_thumbnail(self, asset_id, size):  # noqa: ARG002
                return b"jpeg"

        # WHY: the Immich server; the point of the test is how many clients open.
        monkeypatch.setattr(step2_loading, "SyncImmichClient", _Client)
        # WHY: NiceGUI's worker pool; run the callable directly instead.
        monkeypatch.setattr(step2_loading.run, "io_bound", _call_directly)

        clips = []
        for i in range(35):
            clip = MagicMock()
            clip.asset.id = f"asset-{i}"
            clips.append(clip)
        cache = MagicMock()
        state = MagicMock(immich_url="http://immich.test", immich_api_key="k")

        await step2_loading._fetch_thumbnails_batched(
            clips, cache, state, MagicMock(), None, 0, len(clips), 10
        )

        assert built == [1], f"{len(built)} clients opened for 4 batches"
        assert cache.put.call_count == 35
