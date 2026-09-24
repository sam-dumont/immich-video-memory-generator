"""A local run decodes its film once, on the bytes it publishes, within its encode time."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.config_loader import Config
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from tests.conftest import make_clip
from tests.output_tools_fake import decoded_file_of, is_decode_check, output_tools

_ENCODE_SECONDS = 38_946.0


class _Clock:
    """A monotonic clock that only moves while the fake assembler encodes."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _payload() -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_frames": "134589",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "4486.3",
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


@pytest.fixture
def render_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A one-clip local render whose encode takes eleven hours on the fake clock."""
    from immich_memories import generate_render as generate_render_module
    from immich_memories.generate import GenerationParams
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    clock = _Clock()

    class Assembler:
        def assemble_with_titles(self, _clips, output_path: Path, _callback, **_kwargs) -> Path:
            clock.now += _ENCODE_SECONDS
            output_path.write_bytes(b"assembled-video")
            return output_path

    config = Config(cache={"directory": str(tmp_path / "cache")})
    config.cache.video_cache_enabled = False
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        no_music=True,
    )
    # WHY: an eleven-hour encode is measured on a clock only the fake assembler moves.
    monkeypatch.setattr(generate_render_module, "_time", clock)
    # WHY: extraction downloads the sources from Immich.
    monkeypatch.setattr(generate_render_module, "extract_clips", lambda *_a, **_k: [clip])
    # WHY: settings and the assembler are the FFmpeg-backed encode; the fake one only takes time.
    monkeypatch.setattr(
        generate_render_module,
        "build_assembly_settings",
        lambda *_a, **_k: AssemblySettings(encoding_plan=_h264_plan()),
    )
    monkeypatch.setattr(generate_render_module, "create_assembler", lambda *_a, **_k: Assembler())
    tracker = MagicMock()
    tracker.run_id = "fixed-run"
    return params, tracker, tmp_path / "memory_fixed-run"


class _Tools:
    """ffprobe and ffmpeg, remembering what each decode read and how long it was given."""

    def __init__(self) -> None:
        self.answer = output_tools(_payload())
        self.decoded: list[bytes] = []
        self.budgets: list[object] = []

    def run(self, command: list[str], **kwargs: object):
        if is_decode_check(command):
            self.decoded.append(decoded_file_of(command).read_bytes())
            self.budgets.append(kwargs["timeout"])
        return self.answer(command, **kwargs)


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> _Tools:
    from immich_memories.processing import output_contract

    tools = _Tools()
    # WHY: ffprobe and ffmpeg are the external processes that read the finished film.
    monkeypatch.setattr(output_contract.subprocess, "run", tools.run)
    return tools


@pytest.fixture
def uploads(render_run, monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    params, _tracker, _run_dir = render_run
    params.client = object()
    params.upload_enabled = True
    sent: list[bytes] = []

    def upload(
        _client: object, video_path: Path, _album: object, _captured_at: object = None
    ) -> dict[str, str]:
        sent.append(video_path.read_bytes())
        return {"asset_id": "asset-1"}

    # WHY: the Immich upload is the write this run ends with.
    monkeypatch.setattr("immich_memories.generate_delivery.upload_to_immich", upload)
    return sent


def test_a_run_without_music_decodes_its_film_once_within_the_encode_time(
    render_run, tools: _Tools, uploads: list[bytes]
) -> None:
    from immich_memories.generate import generate_memory

    params, tracker, run_dir = render_run

    result = generate_memory(params, run_tracker=tracker)

    assert result.read_bytes() == b"assembled-video"
    assert tools.budgets == [_ENCODE_SECONDS]
    assert uploads == [b"assembled-video"]
    assert not (run_dir / "memory.assembling.mp4").exists()


def test_a_run_with_music_decodes_only_the_mixed_film_and_publishes_nothing_before(
    render_run, tools: _Tools, uploads: list[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.generate import generate_memory

    params, tracker, run_dir = render_run
    params.no_music = False
    params.music_path = run_dir.parent / "music.wav"
    params.music_path.write_bytes(b"music")
    published_during_mix: list[bool] = []

    def mix(*, video_path: Path, music_path: Path, output_path: Path, config: object) -> None:
        published_during_mix.append((run_dir / "memory.mp4").exists())
        output_path.write_bytes(video_path.read_bytes() + b"+music")

    # WHY: the FFmpeg audio mix is a write; its output is what the check must read.
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", mix)

    result = generate_memory(params, run_tracker=tracker)

    assert published_during_mix == [False]
    assert tools.decoded == [b"assembled-video+music"]
    assert result.read_bytes() == b"assembled-video+music"
    assert uploads == [b"assembled-video+music"]
    assert sorted(path.name for path in run_dir.glob("memory*.mp4")) == ["memory.mp4"]


def test_a_film_changed_after_its_check_is_decoded_again_before_upload(
    render_run, tools: _Tools, uploads: list[bytes]
) -> None:
    from immich_memories.generate import generate_memory

    params, tracker, _run_dir = render_run
    tracker.complete_artifact.side_effect = lambda path, *_a, **_k: Path(path).write_bytes(
        b"rewritten after the check"
    )

    generate_memory(params, run_tracker=tracker)

    assert tools.decoded == [b"assembled-video", b"rewritten after the check"]
    assert uploads == [b"rewritten after the check"]


def test_a_render_whose_decode_check_runs_out_keeps_its_film(
    render_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.generate import GenerationError, generate_memory
    from immich_memories.processing import output_contract

    params, tracker, run_dir = render_run
    answer = output_tools(_payload())

    def decode_never_finishes(command: list[str], **kwargs: object):
        if is_decode_check(command):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return answer(command, **kwargs)

    # WHY: ffmpeg is the external process; the timeout stands in for a decode past its budget.
    monkeypatch.setattr(output_contract.subprocess, "run", decode_never_finishes)

    with pytest.raises(GenerationError) as caught:
        generate_memory(params, run_tracker=tracker)

    staged = run_dir / "memory.assembling.mp4"
    assert staged.read_bytes() == b"assembled-video"
    assert str(staged) in str(caught.value)
    assert "did not finish within the render's own encode time (10:49:06)" in str(caught.value)
