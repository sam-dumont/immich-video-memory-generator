"""One place that builds a job envelope the way an app holding a certified cut would."""

from uuid import uuid4

WORKER_TOKEN = uuid4().hex
AUTH = {"Authorization": f"Bearer {WORKER_TOKEN}"}
_PLACEHOLDER = {"policy": {}, "timeline": {}, "source_ids": ["x"], "sha256": "0" * 64}


def render_request_body(
    *,
    clips=None,
    memory_key="a-memory",
    target_duration_seconds=30.0,
    api_key="private-immich-test-key",
    url="http://immich.invalid",
    titles=None,
    certified_content_intervals=None,
    output=None,
    options=None,
    memory=None,
):
    """Return a submittable body whose binding really was computed over its own cut."""
    from immich_memories_render_worker.admission import envelope_policy
    from immich_memories_render_worker.models import RenderRequest

    from immich_memories.processing.editorial_timing import bind_editorial_timeline

    clips = clips or [{"asset_id": str(uuid4()), "start": 0, "end": 1, "render_mode": "motion"}]
    body = {
        "memory_key": memory_key,
        "immich": {"url": url, "api_key": api_key},
        "plan": {"clips": clips},
        "memory": {"target_duration_seconds": target_duration_seconds} | (memory or {}),
        "titles": titles or {},
        "options": options or {},
        "timing": _PLACEHOLDER,
        "certified_content_intervals": certified_content_intervals or {},
    }
    if output is not None:
        body["output"] = output
    policy = envelope_policy(RenderRequest.model_validate(body))
    timeline = policy.resolve(
        [{"asset_id": clip["asset_id"], "seconds": clip["end"] - clip["start"]} for clip in clips],
        {clip["asset_id"]: _Source(clip["end"] - clip["start"]) for clip in clips},
    )
    body["timing"] = bind_editorial_timeline(policy, timeline, [clip["asset_id"] for clip in clips])
    return body


class _Source:
    """The two attributes the timeline planner reads off a selected source."""

    def __init__(self, seconds: float):
        self.duration_seconds = seconds
        self.duration = seconds
        self.file_created_at = None
        self.is_photo = False


def stub_artifact(directory, *, encoder="libx264", size="32x32", seconds=1, **extra):
    """A real, tiny, plan-conformant mp4, so the output contract has something to accept."""
    import subprocess

    from immich_memories_render_worker.renderer import RenderArtifact

    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

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
            f"color=blue:s={size}:r=30:d={seconds}",
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
            encoder=encoder,
            encoder_args=("-c:v", encoder),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv420p",
            container="mp4",
        ),
        **extra,
    )


def worker_app(tmp_path, renderer, **settings):
    from immich_memories_render_worker.app import create_app
    from immich_memories_render_worker.settings import WorkerSettings

    return create_app(
        WorkerSettings(
            token=WORKER_TOKEN, immich_url="http://immich.invalid", directory=tmp_path, **settings
        ),
        renderer=renderer,
    )
