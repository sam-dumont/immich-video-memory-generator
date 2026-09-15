"""Render a certified Live cut through real HTTP sources, titles and FFmpeg."""

import array
import subprocess

import pytest
from test_live_contract import LiveAssets, live_body
from test_native_integration import _serve


@pytest.mark.integration
@pytest.mark.parametrize(
    ("through_app", "fallback"),
    [(False, False), (True, False), (True, True)],
    ids=["native", "app-worker", "local-fallback"],
)
def test_native_renders_live_sources_titles_and_audio(tmp_path, through_app, fallback):
    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native import NativeRenderer

    body, material = live_body(
        titles={
            "enabled": True,
            "title": "A day out",
            "title_duration": 1.0,
            "ending_duration": 2.0,
        }
    )
    bodies = {}
    for index, video_id in enumerate(material.video_ids):
        path = tmp_path / f"source-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
                "-f", "lavfi", "-i", f"sine=frequency={440 + index * 440}:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
            ],
            check=True,
        )  # fmt: skip
        bodies[video_id] = path.read_bytes()
    assets = LiveAssets(material)
    metadata = {
        still_id: assets.get_asset(still_id).model_dump(mode="json", by_alias=True)
        for still_id in material.still_ids
    }
    calls = []
    server, thread = _serve(bodies, calls, metadata)
    try:
        body["immich"]["url"] = f"http://127.0.0.1:{server.server_port}"
        body["output"] = {"resolution": "720p"}
        directory = tmp_path / "job"
        directory.mkdir()
        renderer = NativeRenderer()
        if through_app:
            artifact = _through_app(body, directory, fallback=fallback)
        else:
            artifact = renderer.render(
                RenderRequest.model_validate(body), directory, lambda *_: None
            )
        if renderer.health()["accelerated"]:
            assert artifact.encoding_plan.encoder == "h264_nvenc"
        decoded = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(artifact.path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "f32le",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        assert max(abs(value) for value in array.array("f", decoded.stdout)) > 0.05
        assert artifact.probe.duration_seconds == pytest.approx(5.0, abs=0.15)
        assert all(
            f"/api/assets/{video_id}/original" in [row[0] for row in calls]
            for video_id in material.video_ids
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _through_app(body, directory, *, fallback):
    from contextlib import nullcontext

    from immich_memories_render_worker.models import RenderRequest
    from immich_memories_render_worker.native_plan import generation_params
    from immich_memories_render_worker.renderer import RenderArtifact

    from conftest import WORKER_TOKEN, running_worker
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.generate import generate_memory
    from immich_memories.processing.output_contract import validate_output
    from immich_memories.tracking import RunTracker

    worker = (
        nullcontext("http://127.0.0.1:1")
        if fallback
        else running_worker(directory / "worker", body["immich"]["url"])
    )
    with SyncImmichClient(body["immich"]["url"], body["immich"]["api_key"]) as client:
        params = generation_params(
            RenderRequest.model_validate(body), directory, client, lambda *_: None
        )
        params.clips[0].audio_categories = ["speech", "music"]
        tracker = RunTracker(
            "app-live-test", db_path=params.config.cache.database_path, capture_system=False
        )
        with worker as url:
            params.config.render = RenderWorkerConfig(
                worker_base_url=url, worker_token=WORKER_TOKEN, fallback_to_local=fallback
            )
            result = generate_memory(params, run_tracker=tracker, defer_finalization=True)
    assert result.assembly_clips[0].duration == 2.0
    assert result.music_mute_windows == [(1.0, 3.0)]
    return RenderArtifact(
        result.path, result.encoding_plan, probe=validate_output(result.path, result.encoding_plan)
    )
