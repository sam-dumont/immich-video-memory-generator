"""Real source HTTP and FFmpeg, with only the GPU capability boundary substituted."""

import http.server
import json
import subprocess
import threading
from uuid import uuid4

import pytest


@pytest.mark.integration
def test_native_fetches_original_directly_and_withholds_software_fallback(tmp_path):
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native import NativeRenderer

    from immich_memories.processing.hardware_detection import detect_hardware_acceleration

    if detect_hardware_acceleration("nvidia").cuda_available:
        pytest.skip("This integration verifies software fallback on a non-NVIDIA host")

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=320x180:r=30:d=3",
            "-vf",
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    asset_id = str(uuid4())
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("x-api-key")))
            if self.path.endswith("/original"):
                body = source.read_bytes()
            elif self.path.endswith("/version"):
                body = json.dumps({"major": 2, "minor": 6, "patch": 0}).encode()
            else:
                body = json.dumps(
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
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "video/mp4" if self.path.endswith("/original") else "application/json",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = RenderRequest.model_validate(
            {
                "request_id": str(uuid4()),
                "memory_key": "example",
                "immich": {
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "api_key": "scoped-test-key",
                },
                "plan": {
                    "clips": [{"asset_id": asset_id, "start": 0, "end": 3, "render_mode": "motion"}]
                },
                "output": {"resolution": "720p"},
            }
        )
        # WHY: advertise GPU capability to reach real rendering on a non-NVIDIA test host.
        # The final encoder check must still reject the software artifact.
        renderer = NativeRenderer(
            capabilities=lambda: {"titles": "CUDA", "encoders": ["h264_nvenc"]}
        )
        job = tmp_path / "job"
        job.mkdir()
        with pytest.raises(RuntimeError, match="fell back from the requested NVENC encoder"):
            renderer.render(request, job, lambda *_: None)
        assert (f"/api/assets/{asset_id}/original", "scoped-test-key") in calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
