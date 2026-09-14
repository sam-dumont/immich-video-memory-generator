"""The cache settings page clears regenerable media without deleting run data."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from immich_memories.config_models import CacheConfig
from immich_memories.ui.pages import step1_cache


def test_cache_controls_only_clear_media(tmp_path, monkeypatch):
    cache = CacheConfig(directory=str(tmp_path / "cache"), database=str(tmp_path / "cache.db"))
    annotations = cache.cache_path / "annotations.sqlite"
    annotations.parent.mkdir(parents=True)
    annotations.write_bytes(b"saved annotations")
    with sqlite3.connect(cache.database_path) as connection:
        connection.execute("CREATE TABLE saved_run (id TEXT)")
        connection.execute("INSERT INTO saved_run VALUES ('keep-me')")

    preview_dir = cache.cache_path / "preview-cache"
    media = [
        cache.video_cache_path / "ab" / "asset.mp4",
        cache.cache_path / "thumbnails" / "ab" / "asset_preview.jpg",
        preview_dir / "asset.mp4",
    ]
    for path in media:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached media")

    monkeypatch.setattr("immich_memories.config.get_config", lambda: SimpleNamespace(cache=cache))
    other_preview = tmp_path / "home/.immich-memories/cache/preview-cache/keep.mp4"
    other_preview.parent.mkdir(parents=True)
    other_preview.write_bytes(b"another installation")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ui = MagicMock()
    monkeypatch.setattr(step1_cache, "ui", ui)

    async def immediate(function):
        return function()

    monkeypatch.setattr(step1_cache, "io_bound_result", immediate)
    step1_cache.render_cache_management()
    refresh = ui.timer.call_args.args[1]
    asyncio.run(refresh())

    labels = [call.args[0] for call in ui.label.call_args_list]
    assert {label for label in labels if label.endswith(" cache")} == {
        "Video cache",
        "Thumbnail cache",
        "Preview cache",
    }
    clear = next(
        call.kwargs["on_click"]
        for call in ui.button.call_args_list
        if call.kwargs.get("icon") == "delete_sweep"
    )
    asyncio.run(clear())

    assert all(not path.exists() for path in media)
    assert annotations.read_bytes() == b"saved annotations"
    assert other_preview.read_bytes() == b"another installation"
    with sqlite3.connect(cache.database_path) as connection:
        assert connection.execute("SELECT id FROM saved_run").fetchall() == [("keep-me",)]
