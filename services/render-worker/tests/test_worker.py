"""HTTP contracts for the render worker, with no external Immich or GPU required."""

import json
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from immich_memories_render_worker.renderer import RenderArtifact

from conftest import AUTH, WORKER_TOKEN, render_request_body, stub_artifact, worker_app


def _settle(client, job_id, *, until=frozenset({"ready", "failed"}), tries=400):
    for _ in range(tries):
        response = client.get(f"/jobs/{job_id}")
        if response.status_code != 200 or response.json()["state"] in until:
            return response
        time.sleep(0.02)
    return response


def test_worker_health_requires_its_token_and_reports_renderer_capabilities(tmp_path):
    class Renderer:
        def health(self):
            return {"ready": True, "accelerated": True, "encoders": ["h264_nvenc"]}

    with TestClient(worker_app(tmp_path, Renderer())) as client:
        assert client.get("/health").status_code == 401
        response = client.get("/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["accelerated"] is True
    assert response.json()["worker_id"]
    assert response.json()["started_at"]


def test_health_remains_responsive_while_the_gpu_lane_is_rendering(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    started = threading.Event()
    release = threading.Event()

    class Renderer:
        def health(self):
            return {"ready": True, "accelerated": True}

        def render(self, request, directory, progress):
            started.set()
            release.wait(timeout=5)
            return stub_artifact(directory)

    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        assert client.get("/health").status_code == 200
        client.post("/jobs", json=render_request_body())
        assert started.wait(timeout=2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            health = pool.submit(client.get, "/health")
            try:
                assert health.result(timeout=1).json()["ready"] is True
            finally:
                release.set()


def test_job_is_idempotent_validated_and_downloaded_only_once(tmp_path):
    class Renderer:
        calls = 0

        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            self.calls += 1
            progress("assembly", 0.5, "Rendering")
            return stub_artifact(
                directory,
                music_mute_windows=[(0.0, 1.0)],
                degradations=("h264_nvenc is not available on this host",),
            )

    renderer = Renderer()
    body = render_request_body()
    with TestClient(worker_app(tmp_path, renderer), headers=AUTH) as client:
        submitted = client.post("/jobs", json=body)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        assert client.post("/jobs", json=body).json()["job_id"] == job_id
        status = _settle(client, job_id)
        assert status.json()["state"] == "ready", status.text
        assert "private-immich-test-key" not in status.text
        assert "private-immich-test-key" not in json.dumps(submitted.json())
        film = client.get(f"/jobs/{job_id}/output")
        assert film.status_code == 200
        assert film.headers["content-type"] == "video/mp4"
        assert len(film.content) > 100
        assert client.get(f"/jobs/{job_id}/output").status_code == 410
        assert renderer.calls == 1


def test_the_result_carries_the_plan_the_probe_and_the_mute_windows(tmp_path):
    """A caller cannot re-run publish_validated_output, or duck music, on bytes alone."""

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            return stub_artifact(
                directory,
                music_mute_windows=[(0.0, 1.0)],
                degradations=("h264_nvenc is not available on this host",),
            )

    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        job_id = client.post("/jobs", json=render_request_body()).json()["job_id"]
        record = _settle(client, job_id).json()
    assert record["state"] == "ready"
    assert record["encoder"] == "libx264"
    assert record["encoding_plan"]["container"] == "mp4"
    assert record["probe"]["size_bytes"] > 0
    assert record["render_metrics"]["encoder"] == "libx264"
    assert record["music_mute_windows"] == [[0.0, 1.0]]
    assert record["degradations"] == ["h264_nvenc is not available on this host"]
    assert record["plan_digest"] and record["submitted_at"] and record["finished_at"]


def _decodes_counted(monkeypatch):
    import subprocess

    real_run = subprocess.run
    decoded: list[str] = []

    def run(command, **kwargs):
        if command[0] == "ffmpeg" and "-progress" in command:
            decoded.append(command[command.index("-i") + 1])
        return real_run(command, **kwargs)

    # WHY: ffmpeg is the external process; the wrapper only counts decodes.
    monkeypatch.setattr(subprocess, "run", run)
    return decoded


def _ready_record(tmp_path, renderer):
    with TestClient(worker_app(tmp_path, renderer), headers=AUTH) as client:
        job_id = client.post("/jobs", json=render_request_body()).json()["job_id"]
        record = _settle(client, job_id).json()
        film = client.get(f"/jobs/{job_id}/output")
    return record, film


def test_a_film_its_renderer_already_decoded_is_not_decoded_again(tmp_path, monkeypatch):
    import base64
    import hashlib

    from immich_memories.processing.output_contract import validate_output

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            artifact = stub_artifact(directory)
            probe = validate_output(artifact.path, artifact.encoding_plan)
            return RenderArtifact(artifact.path, artifact.encoding_plan, probe=probe)

    decoded = _decodes_counted(monkeypatch)
    record, film = _ready_record(tmp_path, Renderer())

    assert record["state"] == "ready", record
    assert len(decoded) == 1
    assert record["probe"]["decoded_frames"] == 30
    assert "stamp" not in record["probe"]
    sha256 = hashlib.sha256(film.content).digest()
    assert record["output_sha256"] == sha256.hex()
    assert film.headers["repr-digest"] == f"sha-256=:{base64.b64encode(sha256).decode()}:"


def test_a_film_its_renderer_did_not_decode_is_decoded_once_here(tmp_path, monkeypatch):
    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            return stub_artifact(directory)

    decoded = _decodes_counted(monkeypatch)
    record, _film = _ready_record(tmp_path, Renderer())

    assert record["state"] == "ready", record
    assert len(decoded) == 1


def test_two_callers_holding_the_same_cut_name_the_same_job(tmp_path):
    """Identity is a fact of the attempt, not a correlation id the caller picked."""

    class Renderer:
        calls = 0

        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            self.calls += 1
            return stub_artifact(directory)

    renderer = Renderer()
    first = render_request_body()
    second = dict(first)
    with TestClient(worker_app(tmp_path, renderer), headers=AUTH) as client:
        one = client.post("/jobs", json=first).json()["job_id"]
        two = client.post("/jobs", json=second).json()["job_id"]
        _settle(client, one)
    assert one == two
    assert renderer.calls == 1


def test_blank_scoped_key_is_rejected_without_echoing_request(tmp_path):
    class Renderer:
        def health(self):
            return {"ready": True}

    body = render_request_body()
    body["immich"]["api_key"] = " "
    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]


def test_bounded_admission_and_failed_job_redact_the_scoped_key(tmp_path):
    release = threading.Event()

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            release.wait(timeout=5)
            progress("render", 0.5, request.immich.api_key.get_secret_value())
            raise RuntimeError("upstream rejected " + request.immich.api_key.get_secret_value())

    app = worker_app(tmp_path, Renderer(), max_jobs=1)
    body = render_request_body()
    with TestClient(app, headers=AUTH) as client:
        try:
            first = client.post("/jobs", json=body)
            job_id = first.json()["job_id"]
            assert client.get(f"/jobs/{job_id}/output").status_code == 409
            other = body | {"immich": {"url": body["immich"]["url"], "api_key": "another-key"}}
            assert client.post("/jobs", json=other).status_code == 409
            assert client.post("/jobs", json=render_request_body(memory_key="x")).status_code == 429
            assert client.get(f"/jobs/{uuid4()}").status_code == 404
            elsewhere = body | {"immich": {"url": "http://elsewhere.invalid", "api_key": "test"}}
            assert client.post("/jobs", json=elsewhere).status_code == 422
        finally:
            release.set()
        response = _settle(client, job_id)
        assert response.json()["state"] == "failed"
        assert "private-immich-test-key" not in response.text
        assert "[redacted]" in response.json()["error"]
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
    assert not list(tmp_path.rglob("*.mp4"))
    records = list(tmp_path.rglob("*.json"))
    assert records
    assert not any("private-immich-test-key" in row.read_text() for row in records)


def test_non_video_artifact_never_becomes_downloadable(tmp_path):
    from immich_memories_render_worker.renderer import RenderArtifact

    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            path = directory / "broken.mp4"
            path.write_bytes(b"an interrupted render")
            return RenderArtifact(
                path,
                EncodingPlan(
                    codec=OutputCodec.H264,
                    encoder="libx264",
                    encoder_args=("-c:v", "libx264"),
                    target_transfer=HdrTransfer.NONE,
                    tone_map_to_sdr=False,
                    pixel_format="yuv420p",
                    container="mp4",
                ),
            )

    with TestClient(worker_app(tmp_path, Renderer()), headers=AUTH) as client:
        job_id = client.post("/jobs", json=render_request_body()).json()["job_id"]
        response = _settle(client, job_id)
        assert response.json()["state"] == "failed"
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
    assert not list(tmp_path.rglob("*.mp4"))


def test_worker_settings_use_the_longer_prefix(monkeypatch, tmp_path):
    """IMMICH_MEMORIES_RENDER_ collides with the app's own render section by one underscore."""
    from immich_memories_render_worker.settings import WorkerSettings

    monkeypatch.setenv("IMMICH_MEMORIES_RENDER_WORKER_TOKEN", WORKER_TOKEN)
    monkeypatch.setenv("IMMICH_MEMORIES_RENDER_WORKER_IMMICH_URL", "http://immich.invalid")
    monkeypatch.setenv("IMMICH_MEMORIES_RENDER_WORKER_DIRECTORY", str(tmp_path))
    assert WorkerSettings().token.get_secret_value() == WORKER_TOKEN
