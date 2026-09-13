"""Where the People page's face crops come from when the cache has none (#824, S7)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from immich_memories.ui import media_route
from immich_memories.ui.media_route import immich_person_face, register_person_route


class _FakeClient:
    """Stands in for SyncImmichClient. WHY: Immich is the external boundary here."""

    calls: list[str] = []
    payload: bytes | None = b"jpeg-bytes"

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get_person_thumbnail(self, person_id: str) -> bytes:
        _FakeClient.calls.append(person_id)
        if self.payload is None:
            raise RuntimeError("no face for this person")
        return self.payload


def _configured(monkeypatch, url: str, api_key: str) -> None:
    config = SimpleNamespace(immich=SimpleNamespace(url=url, api_key=api_key))
    # WHY: the real config comes from disk and the environment; the test fixes it.
    monkeypatch.setattr("immich_memories.config.get_config", lambda: config)
    monkeypatch.setattr("immich_memories.api.sync_client.SyncImmichClient", _FakeClient)
    _FakeClient.calls = []


def test_a_configured_immich_hands_back_the_face(monkeypatch) -> None:
    _configured(monkeypatch, "http://immich.test", "key")
    _FakeClient.payload = b"jpeg-bytes"

    assert immich_person_face("abc") == b"jpeg-bytes"
    assert _FakeClient.calls == ["abc"]


def test_a_person_without_a_face_is_none_not_an_error(monkeypatch) -> None:
    _configured(monkeypatch, "http://immich.test", "key")
    _FakeClient.payload = None

    assert immich_person_face("abc") is None


def test_an_unconfigured_immich_is_never_called(monkeypatch) -> None:
    _configured(monkeypatch, "", "")

    assert immich_person_face("abc") is None
    assert _FakeClient.calls == []


def test_without_a_session_cache_the_route_is_a_404() -> None:
    app = FastAPI()
    register_person_route(app, lambda: None, lambda _person_id: b"jpeg")

    assert TestClient(app).get(media_route.person_thumbnail_url("abc")).status_code == 404
