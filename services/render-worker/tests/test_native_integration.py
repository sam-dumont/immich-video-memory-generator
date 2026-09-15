"""Real source HTTP and FFmpeg, with only the GPU capability boundary substituted."""

import http.server
import json
import subprocess
import threading
from uuid import uuid4

import pytest

from conftest import render_request_body

# WHY: advertise GPU capability so a non-NVIDIA test host reaches the real render
# path. The film still has to come out, on whatever encoder the box actually has.
GPU = {"titles": "CUDA", "encoders": ["h264_nvenc"]}


def _source_clip(path, seconds=3):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=blue:s=320x180:r=30:d={seconds}",
            "-vf",
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _serve(bodies_by_id, calls, metadata=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("x-api-key")))
            asset_id = self.path.split("/assets/")[-1].split("/")[0]
            if self.path.endswith("/original"):
                body, kind = bodies_by_id[asset_id], "video/mp4"
            elif self.path.endswith("/version"):
                body, kind = json.dumps({"major": 2, "minor": 6, "patch": 0}).encode(), "app/json"
            else:
                body = (
                    json.dumps(metadata[asset_id]).encode() if metadata else _asset_json(asset_id)
                )
                kind = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _asset_json(asset_id):
    return json.dumps(
        {
            "id": asset_id,
            "type": "VIDEO",
            "duration": "00:00:03",
            "originalFileName": "source.mp4",
            "width": 320,
            "height": 180,
            "fileCreatedAt": "2026-01-01T12:00:00Z",
            "fileModifiedAt": "2026-01-01T12:00:00Z",
            "updatedAt": "2026-01-01T12:00:00Z",
        }
    ).encode()


def _render(tmp_path, bodies, clips):
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native import NativeRenderer

    calls = []
    server, thread = _serve(bodies, calls)
    try:
        body = render_request_body(
            clips=clips,
            url=f"http://127.0.0.1:{server.server_port}",
            api_key="scoped-test-key",
            output={"resolution": "720p"},
        )
        job = tmp_path / "job"
        job.mkdir()
        artifact = NativeRenderer(capabilities=lambda: GPU).render(
            RenderRequest.model_validate(body), job, lambda *_: None
        )
        return artifact, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.integration
def test_native_fetches_originals_directly_and_renders_on_the_software_encoder(tmp_path):
    from immich_memories.processing.hardware_detection import detect_hardware_acceleration

    if detect_hardware_acceleration("nvidia").cuda_available:
        pytest.skip("This integration proves the software path on a non-NVIDIA host")

    asset_id = str(uuid4())
    bodies = {asset_id: _source_clip(tmp_path / "source.mp4")}
    clips = [{"asset_id": asset_id, "start": 0, "end": 3, "render_mode": "motion"}]
    artifact, calls = _render(tmp_path, bodies, clips)

    assert (f"/api/assets/{asset_id}/original", "scoped-test-key") in calls
    assert artifact.path.exists()
    assert artifact.probe.size_bytes > 0
    assert artifact.encoding_plan.encoder != "h264_nvenc"
    assert any("h264_nvenc" in note for note in artifact.degradations)
    assert any(artifact.encoding_plan.encoder in note for note in artifact.degradations)


@pytest.mark.integration
def test_a_dropped_selected_asset_fails_the_job_rather_than_returning_another_film(tmp_path):
    from immich_memories.processing.hardware_detection import detect_hardware_acceleration

    if detect_hardware_acceleration("nvidia").cuda_available:
        pytest.skip("This integration proves the software path on a non-NVIDIA host")

    kept, dropped = str(uuid4()), str(uuid4())
    bodies = {kept: _source_clip(tmp_path / "source.mp4"), dropped: b""}
    clips = [
        {"asset_id": kept, "start": 0, "end": 3, "render_mode": "motion"},
        {"asset_id": dropped, "start": 0, "end": 3, "render_mode": "motion"},
    ]
    with pytest.raises(Exception) as failure:
        _render(tmp_path, bodies, clips)
    assert "Editorial selected content changed before assembly" in str(failure.value)
    assert dropped in str(failure.value)
