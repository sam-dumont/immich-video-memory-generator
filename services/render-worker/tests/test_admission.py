"""The binding gate the design put in front of the worker, before any media work."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, render_request_body, worker_app


class _Renderer:
    def health(self):
        return {"ready": True}


class _OneAsset:
    """Immich's asset read, the one boundary the binding gate does not exercise."""

    def __init__(self, asset):
        self._asset = asset

    def get_asset(self, _asset_id):
        return self._asset


def test_an_envelope_that_drifted_from_its_binding_is_refused(tmp_path):
    body = render_request_body()
    body["plan"]["clips"].append(
        {"asset_id": str(uuid4()), "start": 0, "end": 2, "render_mode": "motion"}
    )
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 409
    assert "selection changed" in response.json()["detail"]


def test_a_tampered_binding_is_refused(tmp_path):
    body = render_request_body()
    body["timing"]["timeline"]["content_budget"] += 1.0
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 409


def test_a_changed_title_budget_is_refused(tmp_path):
    """The binding froze the title durations; a worker that re-derived them would drift."""
    body = render_request_body()
    body["titles"] = {"enabled": True, "title": "A day out"}
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 409


def test_an_envelope_without_the_certified_intervals_key_is_refused(tmp_path):
    body = render_request_body()
    del body["certified_content_intervals"]
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 422


def test_certified_live_intervals_are_refused_rather_than_silently_dropped(tmp_path):
    body = render_request_body()
    body["certified_content_intervals"] = {body["plan"]["clips"][0]["asset_id"]: [0.0, 1.0]}
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 409
    assert "matching carrier certificates" in response.json()["detail"]


def test_a_matching_envelope_reaches_the_renderer_with_the_bound_timeline(tmp_path):
    """The binding, not the worker's own config, decides the title durations."""
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native_plan import generation_params

    from immich_memories.api.models import Asset, AssetType
    from immich_memories.processing.editorial_timing import prepare_certified_timeline

    body = render_request_body(titles={"enabled": True, "title": "A day out"})
    request = RenderRequest.model_validate(body)
    asset_id = body["plan"]["clips"][0]["asset_id"]
    # WHY: Immich's asset read is the one external boundary this gate does not need.
    client = _OneAsset(
        Asset(
            id=asset_id,
            type=AssetType.VIDEO,
            originalFileName="source.mp4",
            fileCreatedAt="2026-01-01T12:00:00Z",
            fileModifiedAt="2026-01-01T12:00:00Z",
            updatedAt="2026-01-01T12:00:00Z",
            duration="00:00:01",
        )
    )
    params = generation_params(request, tmp_path, client, lambda *_: None)
    prepare_certified_timeline(params)
    assert params.timeline_plan is not None
    assert params.timeline_plan.title_duration == body["timing"]["timeline"]["title_duration"]


def test_a_cut_replanned_under_a_different_policy_is_refused():
    from immich_memories_render_worker.admission import EnvelopeDrift, certify_envelope
    from immich_memories_render_worker.models import RenderRequest

    body = render_request_body(target_duration_seconds=30.0)
    body["memory"]["target_duration_seconds"] = 45.0
    with pytest.raises(EnvelopeDrift):
        certify_envelope(RenderRequest.model_validate(body))
