"""The app consumes a worker result through its authenticated HTTP API."""

import pytest
from fastapi.testclient import TestClient
from test_app_handoff import manual_params

from conftest import WORKER_TOKEN, stub_artifact, worker_app


def test_remote_client_returns_a_validated_film_and_exact_cut_metadata(tmp_path):
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.processing.remote_render import RemoteRenderClient

    params = manual_params(tmp_path)
    asset_id = params.clips[0].asset.id
    seen = []

    class Renderer:
        def health(self):
            return {"ready": True, "accelerated": False, "encoders": []}

        def render(self, request, directory, progress):
            seen.append(request)
            progress("assembly", 0.5, "Rendering")
            return stub_artifact(
                directory,
                size="720x720",
                seconds=3.25,
                clips=({"asset_id": asset_id, "duration": 3.25, "is_photo": False},),
                music_mute_windows=[(0.5, 1.5)],
            )

    settings = RenderWorkerConfig(worker_base_url="http://127.0.0.1", worker_token=WORKER_TOKEN)
    progress = []
    with TestClient(worker_app(tmp_path / "worker", Renderer())) as http:
        artifact = RemoteRenderClient(settings, client=http).render(
            params, tmp_path / "received.mp4", lambda *event: progress.append(event)
        )
        repeated = RemoteRenderClient(settings, client=http).render(
            params, tmp_path / "repeated.mp4", lambda *_: None
        )
        repeated.publish()
        assert repeated.path.is_file()
    assert not artifact.path.exists()
    artifact.publish()
    assert artifact.path.is_file()
    assert artifact.encoding_plan.codec.value == "h264"
    assert [(clip.asset_id, clip.duration) for clip in artifact.assembly_clips] == [
        (asset_id, 3.25)
    ]
    assert artifact.music_mute_windows == [(0.5, 1.5)]
    assert [(clip.start, clip.end) for clip in seen[0].plan.clips] == [(2.5, 5.75)]
    assert progress


def test_normal_generation_uses_the_worker_and_completes_the_local_run(tmp_path, monkeypatch):
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.generate import generate_memory
    from immich_memories.processing import remote_render
    from immich_memories.tracking import RunTracker

    params = manual_params(tmp_path)
    params.config.cache.directory = str(tmp_path / "cache")
    params.config.cache.database = str(tmp_path / "cache" / "runs.sqlite")
    params.config.render = RenderWorkerConfig(
        worker_base_url="http://127.0.0.1", worker_token=WORKER_TOKEN
    )
    params.no_music = True
    seen = []

    class Renderer:
        def health(self):
            return {"ready": True, "accelerated": False}

        def render(self, request, directory, progress):
            seen.append(request)
            return stub_artifact(
                directory,
                size="720x720",
                seconds=3.25,
                clips=(
                    {"asset_id": params.clips[0].asset.id, "duration": 3.25, "is_photo": False},
                ),
            )

    params.config.cache.cache_path.mkdir(parents=True)
    tracker = RunTracker(
        "app-worker-test", db_path=params.config.cache.database_path, capture_system=False
    )
    with TestClient(worker_app(tmp_path / "worker", Renderer())) as http:
        # WHY: keep the real authenticated API while replacing the TCP connection boundary.
        monkeypatch.setattr(remote_render.httpx, "Client", lambda **_kwargs: http)
        result = generate_memory(params, run_tracker=tracker)

    assert result.is_file()
    assert len(seen) == 1
    run = tracker.db.get_run(tracker.run_id)
    assert run.status == "completed"
    assert run.clips_selected == 1


@pytest.mark.parametrize("changed", ["film_duration", "clip_duration", "audio_window"])
def test_a_changed_worker_result_cannot_replace_a_good_output(tmp_path, changed):
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.generate import GenerationError
    from immich_memories.processing.remote_render import RemoteRenderClient

    params = manual_params(tmp_path)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"previous good film")

    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            return stub_artifact(
                directory,
                size="720x720",
                seconds=1 if changed == "film_duration" else 3.25,
                clips=(
                    {
                        "asset_id": params.clips[0].asset.id,
                        "duration": 1 if changed == "clip_duration" else 3.25,
                        "is_photo": False,
                    },
                ),
                music_mute_windows=[(9, 10)] if changed == "audio_window" else None,
            )

    settings = RenderWorkerConfig(worker_base_url="http://127.0.0.1", worker_token=WORKER_TOKEN)
    with (
        TestClient(worker_app(tmp_path / "worker", Renderer())) as http,
        pytest.raises(GenerationError, match="worker"),
    ):
        RemoteRenderClient(settings, client=http).render(params, output, lambda *_: None)
    assert output.read_bytes() == b"previous good film"
    assert not list(tmp_path.glob("*.receiving.mp4"))


class _CountingTools:
    """Real ffprobe and ffmpeg, counting the decodes that read a received film."""

    def __init__(self, *, time_out_here: bool = False):
        import subprocess

        self.real_run = subprocess.run
        self.time_out_here = time_out_here
        self.local_decodes = 0

    def run(self, command, **kwargs):
        import subprocess

        source = command[command.index("-i") + 1] if "-i" in command else ""
        if command[0] == "ffmpeg" and "-progress" in command and ".receiving." in source:
            self.local_decodes += 1
            if self.time_out_here:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return self.real_run(command, **kwargs)


def _stub_renderer(params):
    class Renderer:
        def health(self):
            return {"ready": True}

        def render(self, request, directory, progress):
            return stub_artifact(
                directory,
                size="720x720",
                seconds=3.25,
                clips=(
                    {"asset_id": params.clips[0].asset.id, "duration": 3.25, "is_photo": False},
                ),
            )

    return Renderer()


def _receive(tmp_path, params, monkeypatch, tools, digest=None, on_response=None):
    import subprocess

    from immich_memories_render_worker import jobs

    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.processing.remote_render import RemoteRenderClient

    settings = RenderWorkerConfig(worker_base_url="http://127.0.0.1", worker_token=WORKER_TOKEN)
    # WHY: ffmpeg is the external process; the wrapper only counts the app's own decodes.
    monkeypatch.setattr(subprocess, "run", tools.run)
    if digest is not None:
        # WHY: stands in for a worker that sends no digest, or a download that lost bytes.
        monkeypatch.setattr(jobs, "file_sha256", lambda _path: digest(_path))
    with TestClient(worker_app(tmp_path / "worker", _stub_renderer(params))) as http:
        if on_response is not None:
            # WHY: a response hook stands in for a worker that sends a different header.
            monkeypatch.setattr(http, "event_hooks", {"request": [], "response": [on_response]})
        return RemoteRenderClient(settings, client=http).render(
            params, tmp_path / "received.mp4", lambda *_: None
        )


def test_a_worker_film_whose_digest_matches_is_not_decoded_again_here(tmp_path, monkeypatch):
    params = manual_params(tmp_path)
    tools = _CountingTools()

    artifact = _receive(tmp_path, params, monkeypatch, tools)
    probe = artifact.publish()

    assert tools.local_decodes == 0
    assert probe.decoded_frames == round(3.25 * 30)
    assert artifact.path.is_file()
    assert not list(tmp_path.glob("*.receiving.mp4"))


def test_a_worker_film_that_arrives_without_a_digest_is_decoded_once_here(tmp_path, monkeypatch):
    params = manual_params(tmp_path)
    tools = _CountingTools()

    artifact = _receive(tmp_path, params, monkeypatch, tools, digest=lambda _path: None)
    artifact.publish()

    assert tools.local_decodes == 1
    assert artifact.path.is_file()


def test_a_worker_film_that_does_not_match_its_digest_is_refused(tmp_path, monkeypatch):
    from immich_memories.generate import GenerationError

    params = manual_params(tmp_path)

    with pytest.raises(GenerationError, match="does not match"):
        _receive(tmp_path, params, monkeypatch, _CountingTools(), digest=lambda _path: "0" * 64)

    assert not list(tmp_path.glob("*.receiving.mp4"))
    assert not (tmp_path / "received.mp4").exists()


@pytest.mark.parametrize("header", ["sha-256=abc", "sha-256=:not base64!:"])
def test_an_unreadable_worker_digest_is_refused(tmp_path, monkeypatch, header):
    from immich_memories.generate import GenerationError

    params = manual_params(tmp_path)

    def garble(response):
        if response.request.url.path.endswith("/output"):
            response.headers["repr-digest"] = header

    with pytest.raises(GenerationError, match="unreadable digest"):
        _receive(tmp_path, params, monkeypatch, _CountingTools(), on_response=garble)

    assert not list(tmp_path.glob("*.receiving.mp4"))


def test_a_received_film_that_fails_its_check_here_is_kept(tmp_path, monkeypatch):
    """Hours of worker time are not deleted because this machine could not check them in time."""
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    params = manual_params(tmp_path)
    tools = _CountingTools(time_out_here=True)
    artifact = _receive(tmp_path, params, monkeypatch, tools, digest=lambda _path: None)

    with pytest.raises(InvalidOutputArtifact) as caught:
        artifact.publish()

    kept = list(tmp_path.glob("*.receiving.mp4"))
    assert len(kept) == 1
    assert kept[0].stat().st_size > 0
    assert str(kept[0]) in str(caught.value)
    assert "decode check did not finish within its 15:00 budget" in str(caught.value)
    assert not (tmp_path / "received.mp4").exists()


@pytest.mark.parametrize(
    "failure", ["version", "not_ready", "redirect", "timeout", "failed", "failed_separate_settings"]
)
def test_worker_failures_stop_without_publishing_or_leaking_tokens(tmp_path, failure):
    import json
    from uuid import uuid4

    import httpx

    from immich_memories import __version__
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.generate import GenerationError
    from immich_memories.processing.remote_render import RemoteRenderClient

    params = manual_params(tmp_path)
    settings = RenderWorkerConfig(
        worker_base_url="https://worker.example.com",
        worker_token=WORKER_TOKEN,
        timeout_seconds=0.05,
    )
    if failure != "failed_separate_settings":
        params.config.render = settings
    body = {}
    destinations = []
    job_id = str(uuid4())

    def respond(request):
        destinations.append(request.url.host)
        if request.url.path == "/health":
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://elsewhere.invalid/health"})
            return httpx.Response(
                200,
                json={
                    "app_version": "old" if failure == "version" else __version__,
                    "contract_version": 1,
                    "ready": failure != "not_ready",
                },
            )
        if request.method == "POST":
            body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "job_id": job_id,
                "memory_key": body["memory_key"],
                "plan_digest": body["timing"]["sha256"],
                "state": "queued" if failure == "timeout" else "failed",
                "error": f"Failure {params.config.immich.api_key} {WORKER_TOKEN}",
            },
        )

    # WHY: exercise HTTP failure responses without an external worker or network access.
    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        pytest.raises(GenerationError) as error,
    ):
        RemoteRenderClient(settings, client=http).render(
            params, tmp_path / "film.mp4", lambda *_: None
        )
    assert set(destinations) == {"worker.example.com"}
    assert WORKER_TOKEN not in str(error.value)
    assert params.config.immich.api_key not in str(error.value)
    assert not (tmp_path / "film.mp4").exists()
