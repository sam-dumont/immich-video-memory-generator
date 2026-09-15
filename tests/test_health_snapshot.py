"""Interleaved monitoring requests must not change readiness."""

import threading
from http.server import ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from immich_memories.config_loader import Config, set_config
from tests.e2e.fake_immich import FakeImmichServer, _handler_type


@pytest.fixture(params=[True, False], ids=["authenticated", "rejected-key"])
def health_client(tmp_path, request):
    from immich_memories.ui import health_api
    from immich_memories.ui.app import app

    # Reuse the hermetic Immich HTTP routes; health needs no generated media.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_type({}, {}, [], 0.0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    set_config(
        Config(
            immich={
                "url": f"http://127.0.0.1:{server.server_port}",
                "api_key": FakeImmichServer.api_key if request.param else "rejected-test-key",
            },
            cache={"database": str(tmp_path / "health.db"), "directory": str(tmp_path / "cache")},
        )
    )
    health_api._health_snapshot_cache = None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, request.param
    finally:
        client.close()
        health_api._health_snapshot_cache = None
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("legacy_first", [False, True])
def test_compatibility_health_never_changes_readiness(health_client, legacy_first):
    client, authenticated = health_client
    ready_status = "ready" if authenticated else "degraded"
    legacy_status = "ok" if authenticated else "degraded"
    if legacy_first:
        assert client.get("/health").json()["status"] == legacy_status
    for _ in range(3):
        ready = client.get("/health/ready")
        assert ready.status_code == (200 if authenticated else 503)
        assert ready.json()["status"] == ready_status
        assert ready.json()["immich"]["reachable"] is True
        legacy = client.get("/health")
        assert legacy.status_code == 200
        assert legacy.json() == {**ready.json(), "status": legacy_status}
