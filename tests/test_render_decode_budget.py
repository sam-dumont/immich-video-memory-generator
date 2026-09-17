"""A local render hands its own encode time to every decode check of its film."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.config_loader import Config
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from tests.conftest import make_clip
from tests.output_tools_fake import is_decode_check, output_tools

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
    monkeypatch.setattr(generate_render_module, "_extract_clips", lambda *_a, **_k: [clip])
    # WHY: settings and the assembler are the FFmpeg-backed encode; the fake one only takes time.
    monkeypatch.setattr(
        generate_render_module,
        "_build_assembly_settings",
        lambda *_a, **_k: AssemblySettings(encoding_plan=_h264_plan()),
    )
    monkeypatch.setattr(generate_render_module, "_create_assembler", lambda *_a, **_k: Assembler())
    tracker = MagicMock()
    tracker.run_id = "fixed-run"
    return params, tracker, tmp_path / "memory_fixed-run"


def test_every_decode_check_of_a_local_render_gets_its_encode_time(
    render_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract

    params, tracker, _run_dir = render_run
    calls: list[tuple[list[str], dict[str, object]]] = []
    # WHY: ffprobe and ffmpeg are the external processes that read the finished film.
    monkeypatch.setattr(output_contract.subprocess, "run", output_tools(_payload(), calls=calls))

    result = generate_memory(params, run_tracker=tracker)

    assert result.read_bytes() == b"assembled-video"
    budgets = [kwargs["timeout"] for command, kwargs in calls if is_decode_check(command)]
    assert budgets == [_ENCODE_SECONDS, _ENCODE_SECONDS]


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
