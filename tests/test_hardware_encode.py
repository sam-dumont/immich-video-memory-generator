"""VAAPI and QSV encoders only accept frames that are already on a device."""

from __future__ import annotations

import pathlib

import pytest

from immich_memories.processing.clip_encoder import encoder_args_for_plan
from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    HdrTransfer,
    OutputCodec,
)


def _plan(
    encoder: str, *, pixel_format: str = "yuv420p", codec: OutputCodec = OutputCodec.H264
) -> EncodingPlan:
    return EncodingPlan(
        codec=codec,
        encoder=encoder,
        encoder_args=("-compression_level", "4"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format=pixel_format,
        container="mp4",
        crf=18,
    )


@pytest.mark.parametrize("encoder", ["h264_vaapi", "hevc_vaapi", "h264_qsv", "hevc_qsv"])
def test_device_encoders_never_carry_a_software_pixel_format(encoder: str) -> None:
    args = encoder_args_for_plan(_plan(encoder))

    assert "-pix_fmt" not in args


def test_software_encoders_still_carry_their_pixel_format() -> None:
    args = encoder_args_for_plan(_plan("libx264"))

    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


# ---------------------------------------------------------------------------
# The chain itself
# ---------------------------------------------------------------------------


def _device(cmd: list[str]) -> str:
    return cmd[cmd.index("-init_hw_device") + 1]


def _video_chain(cmd: list[str]) -> str:
    for flag in ("-vf", "-filter:v", "-filter_complex"):
        if flag in cmd:
            return cmd[cmd.index(flag) + 1]
    raise AssertionError(f"command carries no video filter: {cmd}")


def assert_uploads_to_device(cmd: list[str], backend: str) -> None:
    """The contract every VAAPI/QSV command has to satisfy to run at all."""
    assert _device(cmd).startswith(f"{backend}=")
    named = _device(cmd).split("=", 1)[1].split(":", 1)[0]
    assert cmd[cmd.index("-filter_hw_device") + 1] == named
    chain = _video_chain(cmd)
    assert "hwupload" in chain
    assert "-pix_fmt" not in cmd[cmd.index("-c:v") :]


def test_vaapi_simple_filter_uploads_at_the_end_of_the_chain() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    cmd = apply_hardware_encode(
        ["ffmpeg", "-y", "-i", "in.mp4", "-vf", "scale=1920:1080", "-c:v", "h264_vaapi", "out.mp4"]
    )

    assert_uploads_to_device(cmd, "vaapi")
    assert _video_chain(cmd) == "scale=1920:1080,format=nv12,hwupload"


def test_qsv_command_without_a_filter_gains_one() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    cmd = apply_hardware_encode(["ffmpeg", "-y", "-i", "in.mp4", "-c:v", "h264_qsv", "out.mp4"])

    assert_uploads_to_device(cmd, "qsv")
    assert cmd.index("-vf") < cmd.index("-c:v")
    assert "format=qsv" in _video_chain(cmd)


def test_complex_graph_uploads_and_repoints_the_video_map() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    cmd = apply_hardware_encode(
        [
            "ffmpeg", "-y", "-i", "in.mp4",
            "-filter_complex", "[0:v]scale=64:64[vout];[0:a]anull[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "hevc_vaapi", "out.mp4",
        ],
        video_label="[vout]",
    )  # fmt: skip

    assert_uploads_to_device(cmd, "vaapi")
    mapped = cmd[cmd.index("-map") + 1]
    assert mapped != "[vout]"
    assert f"[vout]format=nv12,hwupload{mapped}" in _video_chain(cmd)
    assert "[aout]" in cmd


def test_hdr_plan_uploads_from_a_ten_bit_format() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    cmd = apply_hardware_encode(
        ["ffmpeg", "-i", "in.mp4", "-vf", "null", "-c:v", "hevc_vaapi", "out.mp4"],
        pixel_format="p010le",
    )

    assert "format=p010le,hwupload" in _video_chain(cmd)


def test_software_encoders_are_left_exactly_as_built() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    built = ["ffmpeg", "-i", "in.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "out.mp4"]

    assert apply_hardware_encode(built) == built


def test_an_already_uploaded_chain_is_not_uploaded_twice() -> None:
    from immich_memories.processing.hardware_encode import apply_hardware_encode

    once = apply_hardware_encode(
        ["ffmpeg", "-i", "in.mp4", "-vf", "scale=64:64", "-c:v", "h264_vaapi", "out.mp4"]
    )
    twice = apply_hardware_encode(once)

    # Both halves have to be idempotent: a second upload would convert frames
    # that are already surfaces, and a second `vaapi=va` is a hard FFmpeg error.
    assert _video_chain(twice).count("hwupload") == 1
    assert twice.count("-init_hw_device") == 1
    assert twice.count("-filter_hw_device") == 1
    assert twice == once


def test_a_complex_graph_without_a_video_label_refuses_to_build() -> None:
    from immich_memories.processing.hardware_encode import (
        HardwareChainUnavailable,
        apply_hardware_encode,
    )

    with pytest.raises(HardwareChainUnavailable):
        apply_hardware_encode(
            ["ffmpeg", "-i", "in.mp4", "-filter_complex", "[0:v]null[v]", "-map", "[v]",
             "-c:v", "h264_vaapi", "out.mp4"]
        )  # fmt: skip


def test_a_pixel_format_the_device_cannot_hold_refuses_to_build() -> None:
    from immich_memories.processing.hardware_encode import (
        HardwareChainUnavailable,
        apply_hardware_encode,
    )

    with pytest.raises(HardwareChainUnavailable):
        apply_hardware_encode(
            ["ffmpeg", "-i", "in.mp4", "-vf", "null", "-c:v", "h264_vaapi", "out.mp4"],
            pixel_format="yuv422p10le",
        )


# ---------------------------------------------------------------------------
# The commands the pipeline actually runs
# ---------------------------------------------------------------------------


def _vaapi_plan(codec: OutputCodec = OutputCodec.H264) -> EncodingPlan:
    encoder = "h264_vaapi" if codec is OutputCodec.H264 else "hevc_vaapi"
    return _plan(encoder, codec=codec)


def _qsv_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="h264_qsv",
        encoder_args=("-preset", "medium"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
        crf=18,
    )


def _assembly_clip(tmp_path):
    from immich_memories.processing.assembly_config import AssemblyClip

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"\x00" * 128)
    return AssemblyClip(path=source, duration=2.0)


@pytest.mark.parametrize(
    ("plan_factory", "backend"),
    [(_vaapi_plan, "vaapi"), (_qsv_plan, "qsv")],
)
def test_single_clip_encode_runs_on_the_device(tmp_path, plan_factory, backend) -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.processing.assembly_config import AssemblySettings
    from immich_memories.processing.clip_encoder import ClipEncoder

    settings = AssemblySettings(encoding_plan=plan_factory())
    # WHY: FFmpeg probes read the source file; the command is what is under test.
    prober = MagicMock()
    prober.has_audio_stream.return_value = False
    prober.probe_framerate.return_value = 30.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)

    # WHY: HDR detection and the encode both shell out to FFmpeg; the command is under test.
    with (
        patch("immich_memories.processing.clip_encoder._detect_hdr_type", return_value=None),
        patch("immich_memories.processing.clip_encoder.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stderr="")
        encoder.encode_single_clip(_assembly_clip(tmp_path), tmp_path / "out.mp4", (640, 360))

    assert_uploads_to_device(run.call_args.args[0], backend)


def test_final_assembly_runs_on_the_device(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.processing.assembly_config import AssemblySettings
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_runner import AssemblyContext

    settings = AssemblySettings(encoding_plan=_vaapi_plan(OutputCodec.H265))
    # WHY: duration estimation reads every clip with ffprobe.
    prober = MagicMock()
    prober.estimate_duration.return_value = 5.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    clip = _assembly_clip(tmp_path)
    context = AssemblyContext(
        target_w=640, target_h=360, pix_fmt="yuv420p", hdr_type="sdr",
        clip_hdr_types=[None], clip_primaries=[None], colorspace_filter="",
        target_fps=30, fade_duration=0.5,
    )  # fmt: skip

    # WHY: FFmpeg runs the assembly; the command handed to it is under test.
    with patch("immich_memories.processing.clip_encoder._run_ffmpeg_with_progress") as run:
        encoder.run_ffmpeg_assembly(
            ["-i", str(clip.path)],
            "[0:v]null[vout];[0:a]anull[aout]",
            "[vout]",
            "[aout]",
            tmp_path / "memory.mp4",
            [clip],
            context,
        )

    assert_uploads_to_device(run.call_args.args[0], "vaapi")


def test_streaming_encoder_runs_on_the_device(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.processing.streaming_assembler import StreamingEncoder

    encoder = StreamingEncoder(tmp_path / "out.mp4", 64, 64, 30, encoding_plan=_qsv_plan())

    # WHY: Popen launches the real encoder; the command is what is under test.
    with patch("immich_memories.processing.streaming_assembler.subprocess.Popen") as popen:
        popen.return_value = MagicMock(stderr=None)
        encoder.start()

    assert_uploads_to_device(popen.call_args.args[0], "qsv")


@pytest.mark.parametrize(
    "render",
    ["create_title_ffmpeg", "create_title_with_effects"],
)
def test_title_render_runs_on_the_device(tmp_path, render: str) -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.titles import renderer_ffmpeg

    # WHY: FFmpeg draws the title; the command is what is under test.
    with patch.object(renderer_ffmpeg.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stderr="")
        getattr(renderer_ffmpeg, render)(
            "Title", "Subtitle", tmp_path / "title.mp4", encoding_plan=_vaapi_plan()
        )

    assert_uploads_to_device(run.call_args.args[0], "vaapi")


def test_the_probe_sets_up_what_a_render_sets_up() -> None:
    """Detection only proves anything if it builds the chain a render builds."""
    from unittest.mock import patch

    from immich_memories.processing import hardware

    # WHY: the probe shells out to FFmpeg; its command is what is under test.
    with patch.object(hardware, "_run_ffmpeg_check", return_value=(True, "")) as check:
        hardware._probe_ffmpeg_encode(["-c:v", "h264_vaapi"], upload="vaapi")

    assert_uploads_to_device(["ffmpeg", *check.call_args.args[0]], "vaapi")


def test_burst_merge_runs_on_the_device(tmp_path) -> None:
    from unittest.mock import patch

    from immich_memories.processing import live_photo_merger

    clips = [tmp_path / "a.mov", tmp_path / "b.mov"]
    for clip in clips:
        clip.write_bytes(b"\x00" * 32)

    # WHY: ffprobe reads each source; the merge command is what is under test.
    with (
        patch.object(live_photo_merger, "_detect_clip_hdr", return_value=False),
        patch.object(live_photo_merger, "probe_clip_has_audio", return_value=True),
        patch.object(live_photo_merger, "burst_fps", return_value=30.0),
        patch.object(live_photo_merger, "burst_encoding_plan", return_value=_vaapi_plan()),
    ):
        cmd = live_photo_merger.build_merge_command(
            clips, [(0.0, 1.0), (0.0, 1.0)], tmp_path / "m.mp4"
        )

    assert_uploads_to_device(cmd, "vaapi")
    assert "[outa]" in cmd


def test_analysis_downscale_runs_on_the_device(tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.processing import downscaler

    source = tmp_path / "source.mp4"
    source.write_bytes(b"\x00" * 32)

    # WHY: this Mac reports VideoToolbox; the Linux answer is what is under test.
    with (
        patch.object(downscaler, "needs_downscaling", return_value=True),
        patch.object(
            downscaler, "fast_encoder_args", return_value=["-c:v", "h264_vaapi", "-qp", "28"]
        ),
        patch.object(downscaler.subprocess, "run") as run,
    ):
        run.return_value = MagicMock(returncode=1, stderr="")
        downscaler.downscale_video(source, 720, tmp_path / "small.mp4")

    assert_uploads_to_device(run.call_args.args[0], "vaapi")


def test_clip_extraction_runs_on_the_device(tmp_path) -> None:
    from unittest.mock import patch

    from immich_memories.config_loader import Config
    from immich_memories.processing.clips import ClipExtractor, ClipSegment
    from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities

    source = tmp_path / "video.mp4"
    source.write_bytes(b"\x00" * 100)
    segment = ClipSegment(
        source_path=source, start_time=1.0, end_time=5.0, asset_id="asset-1", score=0.8
    )
    extractor = ClipExtractor(tmp_path, config=Config())
    caps = HWAccelCapabilities(backend=HWAccelBackend.VAAPI, supports_h264_encode=True)

    # WHY: encoder selection shells out to FFmpeg; the command is under test.
    with patch(
        "immich_memories.processing.clips.get_ffmpeg_encoder",
        return_value=("h264_vaapi", ["-compression_level", "4"]),
    ):
        cmd = extractor._build_reencode_command(segment, tmp_path / "out.mp4", hw_caps=caps)

    assert_uploads_to_device(cmd, "vaapi")


def test_a_backend_that_cannot_encode_the_codec_says_so(caplog) -> None:
    """Gemini Lake VAAPI advertises H.264 encode and no HEVC; the default is H.265."""
    import logging

    from immich_memories.processing.hardware import (
        HWAccelBackend,
        HWAccelCapabilities,
        get_ffmpeg_encoder,
    )

    caps = HWAccelCapabilities(
        backend=HWAccelBackend.VAAPI,
        supports_h264_encode=True,
        supports_h265_encode=False,
    )

    with caplog.at_level(logging.INFO, logger="immich_memories.processing.hardware"):
        encoder, _args = get_ffmpeg_encoder(caps, codec="h265")

    assert encoder == "libx265"
    assert "vaapi" in caplog.text.lower()
    assert "h265" in caplog.text.lower()
