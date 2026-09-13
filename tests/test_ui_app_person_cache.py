"""The face route finds a cache even on a session that never loaded a pool (#824, S7)."""

from __future__ import annotations

from types import SimpleNamespace

from immich_memories.ui import app as ui_app


def test_a_request_without_a_session_gets_no_cache(monkeypatch) -> None:
    monkeypatch.setattr(ui_app, "peek_app_state", lambda: None)

    assert ui_app._session_thumbnail_cache_or_open() is None


def test_a_session_that_already_has_a_cache_keeps_it(monkeypatch) -> None:
    session = SimpleNamespace(thumbnail_cache="the cache")
    monkeypatch.setattr(ui_app, "peek_app_state", lambda: session)

    assert ui_app._session_thumbnail_cache_or_open() == "the cache"


def test_a_fresh_session_opens_its_cache_on_first_need(monkeypatch) -> None:
    session = SimpleNamespace(thumbnail_cache=None)
    monkeypatch.setattr(ui_app, "peek_app_state", lambda: session)

    def open_cache(state) -> None:
        state.thumbnail_cache = "opened"

    # WHY: the real opener reads the cache directory from the config on disk.
    monkeypatch.setattr("immich_memories.ui.pages.step2_loading.ensure_caches", open_cache)

    assert ui_app._session_thumbnail_cache_or_open() == "opened"
