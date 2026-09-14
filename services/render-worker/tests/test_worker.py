"""HTTP contracts for the render worker, with no external Immich or GPU required."""

import json
import subprocess
import time
from uuid import uuid4

from fastapi.testclient import TestClient

WORKER_TOKEN = uuid4().hex


def request_body():
    return {
        "request_id": str(uuid4()),
        "memory_key": "a-memory",
        "immich": {"url": "http://immich.invalid", "api_key": "private-immich-test-key"},
        "plan": {
            "clips": [{"asset_id": str(uuid4()), "start": 0, "end": 1, "render_mode": "motion"}]
        },
    }


def test_worker_health_requires_its_token_and_reports_renderer_capabilities(tmp_path):
    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.settings import WorkerSettings

    class Renderer:
        def health(self):
            return {"ready": True, "encoder": "h264_nvenc", "titles": "CUDA"}

    app = create_app(
        WorkerSettings(token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path),
        renderer=Renderer(),
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        response = client.get("/health", headers={"Authorization": f"Bearer {WORKER_TOKEN}"})
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["encoder"] == "h264_nvenc"


def test_job_is_idempotent_validated_and_downloaded_only_once(tmp_path):
    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.renderer import RenderArtifact
    from immich_memories_render_worker.settings import WorkerSettings

    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    class Renderer:
        calls = 0

        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            self.calls += 1
            progress("assembly", 0.5, "Rendering")
            path = directory / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=32x32:r=2:d=1",
                    "-vf",
                    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-color_trc",
                    "bt709",
                    "-color_primaries",
                    "bt709",
                    str(path),
                ],
                check=True,
                capture_output=True,
            )
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

    renderer = Renderer()
    app = create_app(
        WorkerSettings(token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path),
        renderer=renderer,
    )
    headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}
    body = request_body()
    with TestClient(app, headers=headers) as client:
        submitted = client.post("/jobs", json=body)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        assert client.post("/jobs", json=body).json()["job_id"] == job_id
        for _ in range(100):
            status = client.get(f"/jobs/{job_id}")
            if status.json()["state"] in {"ready", "failed"}:
                break
            time.sleep(0.05)
        assert status.json()["state"] == "ready", status.text
        assert "private-immich-test-key" not in status.text
        assert "private-immich-test-key" not in json.dumps(submitted.json())
        film = client.get(f"/jobs/{job_id}/output")
        assert film.status_code == 200
        assert film.headers["content-type"] == "video/mp4"
        assert len(film.content) > 100
        assert client.get(f"/jobs/{job_id}/output").status_code == 410
        assert renderer.calls == 1


def test_blank_scoped_key_is_rejected_without_echoing_request(tmp_path):
    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.settings import WorkerSettings

    class Renderer:
        def health(self):
            return {"ready": True}

    app = create_app(
        WorkerSettings(token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path),
        renderer=Renderer(),
    )
    body = request_body()
    body["immich"]["api_key"] = " "
    with TestClient(app, headers={"Authorization": f"Bearer {WORKER_TOKEN}"}) as client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]


def test_bounded_admission_and_failed_job_redact_the_scoped_key(tmp_path):
    import threading

    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.settings import WorkerSettings

    release = threading.Event()

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            release.wait(timeout=5)
            progress("render", 0.5, request.immich.api_key.get_secret_value())
            raise RuntimeError("upstream rejected " + request.immich.api_key.get_secret_value())

    app = create_app(
        WorkerSettings(
            token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path, max_jobs=1
        ),
        renderer=Renderer(),
    )
    body = request_body()
    with TestClient(app, headers={"Authorization": f"Bearer {WORKER_TOKEN}"}) as client:
        try:
            first = client.post("/jobs", json=body)
            job_id = first.json()["job_id"]
            assert client.get(f"/jobs/{job_id}/output").status_code == 409
            assert client.post("/jobs", json=body | {"memory_key": "different"}).status_code == 409
            assert client.post("/jobs", json=request_body()).status_code == 429
            assert client.get(f"/jobs/{uuid4()}").status_code == 404
            bad_server = body | {"immich": {"url": "http://elsewhere.invalid", "api_key": "test"}}
            assert client.post("/jobs", json=bad_server).status_code == 422
        finally:
            release.set()
        for _ in range(100):
            response = client.get(f"/jobs/{job_id}")
            if response.json()["state"] == "failed":
                break
            time.sleep(0.01)
        assert response.json()["state"] == "failed"
        assert "private-immich-test-key" not in response.text
        assert "[redacted]" in response.json()["error"]
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
    assert not list(tmp_path.rglob("*.mp4"))


def test_non_video_artifact_never_becomes_downloadable(tmp_path):
    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.renderer import RenderArtifact
    from immich_memories_render_worker.settings import WorkerSettings

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

    app = create_app(
        WorkerSettings(token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path),
        renderer=Renderer(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {WORKER_TOKEN}"}) as client:
        job_id = client.post("/jobs", json=request_body()).json()["job_id"]
        for _ in range(100):
            response = client.get(f"/jobs/{job_id}")
            if response.json()["state"] == "failed":
                break
            time.sleep(0.01)
        assert response.json()["state"] == "failed"
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
    assert not list(tmp_path.rglob("*.mp4"))
