"""Compatibility health reads must not change subsequent readiness results."""

import time

from fastapi import FastAPI
from starlette.testclient import TestClient

from immich_memories.ui import health_api


def test_health_request_preserves_a_cached_ready_result(monkeypatch):
    # WHY: seed the dependency snapshot; no external Immich server is needed.
    monkeypatch.setattr(
        health_api, "_health_snapshot_cache", (time.monotonic(), {"status": "ready"})
    )
    server = FastAPI()
    health_api.register_health_routes(server)
    with TestClient(server) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health").json()["status"] == "ok"
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
