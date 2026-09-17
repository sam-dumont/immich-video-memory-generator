"""Job lifecycle: expiry, retry after failure, the deadline, and surviving a restart."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, render_request_body, stub_artifact, worker_app


class _Renderer:
    def health(self):
        return {"ready": True}

    def render(self, request, directory, progress):
        return stub_artifact(directory)


def _job(job_id, **over):
    from immich_memories_render_worker.models import JobStatus

    return JobStatus(
        job_id=job_id,
        memory_key=over.pop("memory_key", "a-cut"),
        plan_digest="a" * 64,
        worker_id=uuid4(),
        submitted_at=datetime.now(UTC),
        **over,
    )


def _service(tmp_path, renderer=None, **over):
    from immich_memories_render_worker.jobs import RenderJobs
    from immich_memories_render_worker.settings import WorkerSettings

    return RenderJobs(
        WorkerSettings(
            token="a-worker-token",  # noqa: S106
            immich_url="http://immich.invalid",
            directory=tmp_path,
            **over,
        ),
        renderer or _Renderer(),
    )


def _settle(client, job_id, state, tries=600):
    for _ in range(tries):
        response = client.get(f"/jobs/{job_id}")
        if response.status_code != 200 or response.json()["state"] == state:
            return response
        time.sleep(0.02)
    return response


def test_only_terminal_jobs_expire_and_the_same_cut_can_be_submitted_again(monkeypatch):
    from immich_memories_render_worker.store import MemoryJobRepository

    now = [100.0]
    # WHY: advance the external clock without holding CI for a retention interval.
    monkeypatch.setattr(time, "time", lambda: now[0])
    store = MemoryJobRepository(2, 60)
    ready_id, running_id = uuid4(), uuid4()
    store.admit(_job(ready_id), "one")
    store.admit(_job(running_id, memory_key="running-cut"), "two")
    store.update(ready_id, state="ready")
    store.update(running_id, state="running")
    now[0] += 61
    assert set(store.expire()) == {ready_id}
    assert store.get(running_id).state == "running"
    status, fresh = store.admit(_job(ready_id, memory_key="new-cut"), "three")
    assert fresh
    assert status.memory_key == "new-cut"


def test_an_expired_job_says_so_rather_than_answering_a_bare_404(tmp_path, monkeypatch):
    from immich_memories_render_worker.store import JobExpired

    service = _service(tmp_path, retention_seconds=60)
    try:
        job_id = uuid4()
        service.store.admit(_job(job_id), "fingerprint")
        service.store.update(job_id, state="failed")
        # WHY: advance the external clock instead of holding CI for the retention window.
        monkeypatch.setattr(time, "time", lambda: datetime.now(UTC).timestamp() + 120)
        service.cleanup()
        with pytest.raises(JobExpired):
            service.store.get(job_id)
    finally:
        service.close()


def test_a_failed_job_can_be_retried_without_waiting_for_retention(tmp_path):
    class Renderer:
        calls = 0

        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient upstream hiccup")
            return stub_artifact(directory)

    renderer = Renderer()
    body = render_request_body()
    with TestClient(worker_app(tmp_path, renderer), headers=AUTH) as client:
        job_id = client.post("/jobs", json=body).json()["job_id"]
        assert _settle(client, job_id, "failed").json()["state"] == "failed"
        assert client.post("/jobs", json=body).status_code == 202
        assert _settle(client, job_id, "ready").json()["state"] == "ready"
    assert renderer.calls == 2


def test_a_render_past_its_deadline_is_abandoned_so_admission_recovers(tmp_path):
    service = _service(tmp_path, job_timeout_seconds=30, max_jobs=1)
    try:
        job_id = uuid4()
        service.store.admit(_job(job_id), "fingerprint")
        service.store.update(
            job_id, state="running", started_at=datetime.now(UTC) - timedelta(seconds=120)
        )
        service.cleanup()
        abandoned = service.store.get(job_id)
        assert abandoned.state == "failed"
        assert "abandoned" in abandoned.error
        assert service.store.admit(_job(uuid4(), memory_key="next-cut"), "other")[1]
    finally:
        service.close()


def test_a_restart_is_distinguishable_from_an_expiry_and_from_a_stranger(tmp_path):
    body = render_request_body()
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        job_id = client.post("/jobs", json=body).json()["job_id"]
        _settle(client, job_id, "ready")
    with TestClient(worker_app(tmp_path, _Renderer()), headers=AUTH) as client:
        restarted = client.get(f"/jobs/{job_id}")
        stranger = client.get(f"/jobs/{uuid4()}")
    assert restarted.status_code == 200
    assert restarted.json()["state"] == "failed"
    assert "restarted" in restarted.json()["error"]
    assert stranger.status_code == 404
    assert stranger.json()["reason"] == "unknown"
    assert stranger.json()["worker_started_at"]


def test_a_job_id_cannot_name_a_directory_outside_the_worker_workspace(tmp_path):
    service = _service(tmp_path)
    try:
        with pytest.raises(ValueError, match="outside"):
            service.directory("../../elsewhere")
        assert service.directory(uuid4()).is_relative_to(service.root)
    finally:
        service.close()


def test_boot_sweeps_a_session_a_hard_kill_left_behind(tmp_path):
    scratch = tmp_path / "render-jobs"
    scratch.mkdir(parents=True)
    stranded = scratch / "session-killed"
    stranded.mkdir()
    (stranded / "original.mp4").write_bytes(b"a downloaded original")

    service = _service(tmp_path)
    try:
        assert not stranded.exists()
    finally:
        service.close()
