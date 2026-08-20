"""Step 2 read its whole thumbnail cache to ask what was in it.

The set of already-cached thumbnails was built with `get_batch(...).keys()`,
which reads every cached thumbnail's *bytes* off disk to answer a question about
which ids exist. On 1583 real thumbnails that is 543 MB read and thrown away,
244 ms against 7 ms -- and at 5000 clips, ~1.7 GB.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.cache.thumbnail_cache import ThumbnailCache


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
