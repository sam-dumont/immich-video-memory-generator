"""Unit tests for streaming assembler components."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _prores_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-profile:v", "3"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-preset", "ultrafast", "-crf", "28"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _hardware_h265_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="hevc_videotoolbox",
        encoder_args=("-q:v", "50"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def test_streaming_hardware_encoder_failure_retries_same_codec_in_software(tmp_path: Path) -> None:
    """A failed H.265 streaming encoder retries once with libx265, never H.264."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    plans: list[EncodingPlan] = []
    effective_plans: list[EncodingPlan] = []

    class FailingHardwareEncoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def finish(self) -> None:
            if self.plan.encoder == "hevc_videotoolbox":
                raise RuntimeError("Unknown encoder 'hevc_videotoolbox'")

    clip = SimpleNamespace(duration=1.0, path=tmp_path / "input.mp4")
    with (
        patch(
            "immich_memories.processing.streaming_assembler.StreamingEncoder",
            FailingHardwareEncoder,
        ),
        patch("immich_memories.processing.streaming_assembler._encode_clip_sequence"),
    ):
        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=tmp_path / "output.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_h265_plan(),
            effective_plan_callback=effective_plans.append,
        )

    assert [plan.encoder for plan in plans] == ["hevc_videotoolbox", "libx265"]
    assert len(plans) == 2
    assert all(plan.codec is OutputCodec.H265 for plan in plans)
    assert [plan.encoder for plan in effective_plans] == ["libx265"]


def test_streaming_broken_pipe_retries_same_codec_in_software(tmp_path: Path) -> None:
    """Early hardware FFmpeg death during frame writing retries once in software."""
    from immich_memories.processing.streaming_assembler import (
        StreamingEncoderWriteError,
        assemble_streaming,
    )

    plans: list[EncodingPlan] = []

    class EarlyFailingHardwareEncoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            self.plan = encoding_plan
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def write_frame(self, _frame: np.ndarray) -> None:
            if self.plan.encoder == "hevc_videotoolbox":
                raise StreamingEncoderWriteError("hardware ffmpeg exited before accepting frames")

        def finish(self) -> None:
            pass

    clip = SimpleNamespace(duration=1.0, path=tmp_path / "input.mp4")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    with (
        patch(
            "immich_memories.processing.streaming_assembler.StreamingEncoder",
            EarlyFailingHardwareEncoder,
        ),
        patch(
            "immich_memories.processing.streaming_assembler._make_decoder",
            side_effect=lambda *_args, **_kwargs: iter([frame]),
        ),
    ):
        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=tmp_path / "output.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_h265_plan(),
        )

    assert [plan.encoder for plan in plans] == ["hevc_videotoolbox", "libx265"]
    assert len(plans) == 2
    assert all(plan.codec is OutputCodec.H265 for plan in plans)


def test_streaming_encoder_write_translates_broken_pipe_to_encoder_failure(tmp_path: Path) -> None:
    """Only the encoder write boundary may classify a broken pipe as retryable."""
    from immich_memories.processing.streaming_assembler import StreamingEncoder

    class ClosedEncoderStdin:
        def write(self, _frame: memoryview) -> None:
            raise BrokenPipeError("hardware ffmpeg exited before accepting frames")

    encoder = StreamingEncoder(
        tmp_path / "output.mp4", width=16, height=16, fps=1, encoding_plan=_hardware_h265_plan()
    )
    encoder._proc = SimpleNamespace(stdin=ClosedEncoderStdin())

    with pytest.raises(RuntimeError) as raised:
        encoder.write_frame(np.zeros((16, 16, 3), dtype=np.uint8))

    assert isinstance(raised.value.__cause__, BrokenPipeError)


def test_streaming_callback_broken_pipe_does_not_retry_software(tmp_path: Path) -> None:
    """A preview-consumer pipe error must escape without an encoder fallback."""
    from immich_memories.processing.streaming_assembler import assemble_streaming

    plans: list[EncodingPlan] = []

    class WorkingEncoder:
        def __init__(self, *_args: object, encoding_plan: EncodingPlan, **_kwargs: object) -> None:
            plans.append(encoding_plan)

        def start(self) -> None:
            pass

        def write_frame(self, _frame: np.ndarray) -> None:
            pass

        def finish(self) -> None:
            pass

    def disconnected_preview(_jpeg: bytes) -> None:
        raise BrokenPipeError("preview consumer disconnected")

    clip = SimpleNamespace(duration=1.0, path=tmp_path / "input.mp4")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    with (
        patch("immich_memories.processing.streaming_assembler.StreamingEncoder", WorkingEncoder),
        patch(
            "immich_memories.processing.streaming_assembler._make_decoder",
            side_effect=lambda *_args, **_kwargs: iter([frame]),
        ),
        pytest.raises(BrokenPipeError, match="preview consumer"),
    ):
        assemble_streaming(
            clips=[clip],
            transitions=[],
            output_path=tmp_path / "output.mp4",
            width=16,
            height=16,
            fps=1,
            encoding_plan=_hardware_h265_plan(),
            frame_preview_callback=disconnected_preview,
        )

    assert [plan.encoder for plan in plans] == ["hevc_videotoolbox"]


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)  # noqa: S603, S607
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@requires_ffmpeg
class TestFrameDecoder:
    def test_yields_frames_with_correct_shape(self, tmp_path: object) -> None:
        """FrameDecoder should yield numpy arrays of (height, width, 3)."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        tmp = Path(str(tmp_path))

        # Create a tiny test clip via FFmpeg
        clip = tmp / "test.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10:duration=0.5",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(clip),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )

        decoder = FrameDecoder(clip, width=320, height=240, fps=10)
        frames = list(decoder)

        assert len(frames) >= 4  # 0.5s * 10fps = 5 frames (allow off-by-one)
        for frame in frames:
            assert frame.shape == (240, 320, 3)
            assert frame.dtype == np.uint8


@requires_ffmpeg
class TestStreamingEncoder:
    def test_encodes_frames_to_valid_mp4(self, tmp_path: object) -> None:
        """StreamingEncoder should produce a valid MP4 from numpy frames."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import StreamingEncoder

        tmp = Path(str(tmp_path))
        output = tmp / "test_output.mp4"
        width, height, fps = 320, 240, 10
        n_frames = 10

        encoder = StreamingEncoder(
            output,
            width,
            height,
            fps,
            encoding_plan=_h264_plan(),
        )
        encoder.start()
        for i in range(n_frames):
            # Gradient frame — different each frame for visual verification
            frame = np.full((height, width, 3), fill_value=i * 25, dtype=np.uint8)
            encoder.write_frame(frame)
        encoder.finish()

        assert output.exists()
        assert output.stat().st_size > 0

        # Verify with ffprobe
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
        assert len(video_streams) == 1
        assert float(probe["format"]["duration"]) > 0.5


class TestFrameBlender:
    def test_crossfade_blend_produces_interpolated_frames(self) -> None:
        """Blending two frames at alpha=0.5 should average pixel values."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.5, out=out, temp=temp)

        # (100 * 0.5 + 200 * 0.5) = 150
        assert np.all(out == 150)

    def test_crossfade_alpha_zero_is_frame_a(self) -> None:
        """Alpha=0 should return frame_a unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=0.0, out=out, temp=temp)
        assert np.all(out == 100)

    def test_crossfade_alpha_one_is_frame_b(self) -> None:
        """Alpha=1 should return frame_b unchanged."""
        from immich_memories.processing.streaming_assembler import blend_crossfade

        frame_a = np.full((4, 4, 3), 100, dtype=np.uint8)
        frame_b = np.full((4, 4, 3), 200, dtype=np.uint8)
        out = np.zeros_like(frame_a)
        temp = np.zeros_like(frame_a)

        blend_crossfade(frame_a, frame_b, alpha=1.0, out=out, temp=temp)
        assert np.all(out == 200)


def test_full_streaming_prores_threads_plan_and_uses_mov_work_video(tmp_path) -> None:
    from unittest.mock import patch

    from immich_memories.processing.streaming_assembler import streaming_assemble_full

    clip = type("Clip", (), {"path": tmp_path / "clip.mp4", "duration": 1.0})()
    plan = _prores_plan()
    with (
        patch(
            "immich_memories.processing.streaming_assembler.assemble_streaming",
            return_value=[],
        ) as assemble,
        patch("immich_memories.processing.streaming_assembler._probe_duration", return_value=1.0),
        patch("immich_memories.processing.streaming_assembler.extract_and_mix_audio"),
        patch("immich_memories.processing.streaming_assembler.mux_video_audio"),
    ):
        streaming_assemble_full(
            clips=[clip],
            transitions=[],
            output_path=tmp_path / "memory.mov",
            width=320,
            height=240,
            fps=30,
            encoding_plan=plan,
        )

    assert assemble.call_args.kwargs["encoding_plan"] is plan
    assert assemble.call_args.kwargs["output_path"].name == "video.mov"


def test_frame_decoder_applies_hdr_to_sdr_color_chain() -> None:
    """Streaming decode must consume conversion, output tags, and pixel format."""
    from immich_memories.processing.streaming_assembler import FrameDecoder

    decoder = FrameDecoder(
        Path("hlg.mp4"),
        width=320,
        height=240,
        fps=30,
        hdr_conversion=(
            ",zscale=t=linear:tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=tv:npl=100"
            ",format=gbrpf32le,tonemap=tonemap=hable:desat=0"
            ",zscale=t=bt709:p=bt709:m=bt709:r=tv"
        ),
        colorspace_filter=(",setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"),
        output_pix_fmt=",format=yuv420p",
    )

    vf = decoder._build_vf()

    assert "zscale=t=linear" in vf
    assert "tonemap=tonemap=hable" in vf
    assert "zscale=t=bt709" in vf
    assert "setparams=colorspace=bt709" in vf
    assert vf.endswith("setsar=1")


def test_streaming_decoder_builds_plan_targeted_hlg_to_sdr_chain() -> None:
    from unittest.mock import MagicMock, patch

    from immich_memories.processing.streaming_assembler import _make_decoder

    clip = MagicMock(path=Path("hlg.mp4"), is_title_screen=False, rotation_override=None)
    ctx = MagicMock(
        hdr_type="sdr",
        pix_fmt="yuv420p",
        clip_hdr_types=["hlg"],
        clip_primaries=["bt2020"],
        colorspace_filter=(",setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"),
    )

    with patch(
        "immich_memories.processing.hdr_utilities._check_zscale_available",
        return_value=True,
    ):
        decoder = _make_decoder(clip, 0, 320, 240, 30, ctx=ctx, hdr_type=None)

    vf = decoder._build_vf()
    assert "zscale=t=linear" in vf
    assert "tonemap=tonemap=hable" in vf
    assert "zscale=t=bt709" in vf
    assert "setparams=colorspace=bt709" in vf
    assert ",format=yuv420p,setsar=1" in vf


@pytest.mark.parametrize(
    ("source_transfer", "target_transfer"),
    [("hlg", "pq"), ("pq", "hlg")],
)
def test_streaming_decoder_fails_when_required_hdr_conversion_is_unavailable(
    source_transfer: str,
    target_transfer: str,
) -> None:
    """Streaming must not relabel HLG as PQ, or PQ as HLG, without conversion."""
    from unittest.mock import MagicMock, patch

    from immich_memories.processing.hdr_utilities import RequiredColorConversionUnavailable
    from immich_memories.processing.streaming_assembler import _make_decoder

    clip = MagicMock(path=Path("hdr.mp4"), is_title_screen=False, rotation_override=None)
    ctx = MagicMock(
        hdr_type=target_transfer,
        pix_fmt="yuv420p10le",
        clip_hdr_types=[source_transfer],
        clip_primaries=["bt2020"],
        colorspace_filter=(
            ",setparams=colorspace=bt2020nc:color_primaries=bt2020:"
            f"color_trc={'smpte2084' if target_transfer == 'pq' else 'arib-std-b67'}"
        ),
    )

    with (
        patch(
            "immich_memories.processing.hdr_utilities._check_zscale_available",
            return_value=False,
        ),
        pytest.raises(RequiredColorConversionUnavailable),
    ):
        _make_decoder(clip, 0, 320, 240, 30, ctx=ctx, hdr_type=target_transfer)


@requires_ffmpeg
class TestStreamingAssemble:
    def test_assembles_two_clips_with_crossfade(self, tmp_path: object) -> None:
        """assemble_streaming should produce valid output from two clips."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        tmp = Path(str(tmp_path))

        # Generate two tiny test clips with audio
        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x240:rate=10:duration=2:alpha={80 + i * 80}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={440 + i * 220}:duration=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        output = tmp / "output.mp4"
        assemble_streaming(
            clips=clips,
            transitions=["fade"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoding_plan=_h264_plan(),
        )

        assert output.exists()
        # Duration should be ~3.7s (2+2-0.3 crossfade)
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        duration = float(probe["format"]["duration"])
        assert 3.0 < duration < 4.5

    def test_assembles_with_cut_transition(self, tmp_path: object) -> None:
        """Cut transitions should concatenate without blending."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        tmp = Path(str(tmp_path))

        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10:duration=1",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=1.0, asset_id=f"test-{i}"))

        output = tmp / "output_cut.mp4"
        assemble_streaming(
            clips=clips,
            transitions=["cut"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoding_plan=_h264_plan(),
        )

        assert output.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        duration = float(probe["format"]["duration"])
        # Two 1s clips with cut = ~2s (no overlap)
        assert 1.5 < duration < 2.5


@requires_ffmpeg
class TestAudioHandling:
    def test_extract_and_mix_audio(self, tmp_path: object) -> None:
        """extract_and_mix_audio should produce a valid audio file with crossfade."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import extract_and_mix_audio

        tmp = Path(str(tmp_path))

        # Create clips with audio
        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10:duration=2",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={440 + i * 220}:duration=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        audio_out = tmp / "mixed_audio.m4a"
        extract_and_mix_audio(
            clips=clips,
            transitions=["fade"],
            output_path=audio_out,
            fade_duration=0.3,
        )

        assert audio_out.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(audio_out),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )
        audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) >= 1

    def test_short_silent_clip_survives_loudness_normalization(self, tmp_path: object) -> None:
        """Short silence must not let loudnorm feed NaNs to the AAC encoder."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import extract_and_mix_audio

        tmp = Path(str(tmp_path))
        silent = tmp / "silent.wav"
        tone = tmp / "tone.wav"
        for output, source in (
            (silent, "anullsrc=r=48000:cl=stereo:d=2"),
            (tone, "sine=frequency=440:sample_rate=48000:duration=2"),
        ):
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    source,
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )

        clips = [
            AssemblyClip(path=silent, duration=2.0, asset_id="silent"),
            AssemblyClip(path=tone, duration=2.0, asset_id="tone"),
        ]
        output = tmp / "normalized.m4a"

        extract_and_mix_audio(
            clips=clips,
            transitions=["cut"],
            output_path=output,
            fps=30,
            normalize_audio=True,
            pre_extracted_audio=[silent, tone],
            video_duration=4.0,
        )

        probe = subprocess.run(  # noqa: S603, S607
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate",
                "-of",
                "default=noprint_wrappers=1",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "codec_name=aac" in probe.stdout
        assert "sample_rate=48000" in probe.stdout


@requires_ffmpeg
class TestFullStreamingPipeline:
    def test_full_pipeline_produces_video_with_audio(self, tmp_path: object) -> None:
        """Full streaming pipeline should produce MP4 with both video and audio."""
        from pathlib import Path

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import streaming_assemble_full

        tmp = Path(str(tmp_path))

        clips = []
        for i in range(3):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x240:rate=10:duration=2:alpha={60 + i * 60}",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={330 + i * 110}:duration=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        output = tmp / "final.mp4"
        streaming_assemble_full(
            clips=clips,
            transitions=["fade", "cut"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoding_plan=_h264_plan(),
        )

        assert output.exists()
        probe = json.loads(
            subprocess.run(  # noqa: S603, S607
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        )

        stream_types = {s["codec_type"] for s in probe["streams"]}
        assert "video" in stream_types
        assert "audio" in stream_types
        assert float(probe["format"]["duration"]) > 3.0


# ---------------------------------------------------------------------------
# Regression tests — verify feature parity with old filter graph pipeline.
# These would have caught the gaps in the initial streaming migration.
# ---------------------------------------------------------------------------


class TestFrameDecoderFilterChain:
    """Verify FrameDecoder builds the correct FFmpeg filter chain."""

    def test_default_filter_includes_pts_and_timebase(self) -> None:
        """PTS reset and timebase are critical for multi-clip concat."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30)
        vf = decoder._build_vf()

        assert "setpts=PTS-STARTPTS" in vf
        assert "settb=1/30" in vf
        assert "setsar=1" in vf

    def test_rotation_90_includes_transpose(self) -> None:
        """90° rotation must apply transpose=1 before scale."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=90)
        vf = decoder._build_vf()

        assert "transpose=1" in vf
        # Transpose must come before scale
        assert vf.index("transpose=1") < vf.index("scale=")

    def test_rotation_180_includes_hflip_vflip(self) -> None:
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=180)
        vf = decoder._build_vf()
        assert "hflip,vflip" in vf

    def test_rotation_270_includes_transpose_2(self) -> None:
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=270)
        vf = decoder._build_vf()
        assert "transpose=2" in vf

    def test_privacy_blur_includes_gblur(self) -> None:
        """Privacy mode must apply heavy gaussian blur."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(
            Path("/fake.mp4"), width=1920, height=1080, fps=30, privacy_blur=True
        )
        vf = decoder._build_vf()
        # min(1920,1080) * 0.04 = 43, frosted glass chain
        assert "gblur=sigma=37" in vf
        assert "noise=alls=15" in vf

    def test_exact_transfer_conversion_is_applied_before_rawvideo_pipe(self) -> None:
        """Per-source transfer normalization must happen before frame blending."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(
            Path("/fake.mp4"),
            width=1920,
            height=1080,
            fps=30,
            hdr_conversion="zscale=t=arib-std-b67:tin=smpte2084",
            colorspace_filter=",setparams=colorspace=bt2020nc",
            output_pix_fmt=",format=p010le",
        )
        vf = decoder._build_vf()

        assert "format=p010le" in vf
        assert "zscale" in vf
        assert "setparams" in vf

    def test_no_rotation_when_zero(self) -> None:
        """rotation=0 should NOT add any transpose filter."""
        from pathlib import Path

        from immich_memories.processing.streaming_assembler import FrameDecoder

        decoder = FrameDecoder(Path("/fake.mp4"), width=1920, height=1080, fps=30, rotation=0)
        vf = decoder._build_vf()
        assert "transpose" not in vf
        assert "hflip" not in vf


class TestAudioFilterChain:
    """Verify audio filter graph includes loudnorm and privacy muffle."""

    def test_loudnorm_included_when_normalize_true(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30, normalize_audio=True)
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph

    def test_loudnorm_excluded_when_normalize_false(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30, normalize_audio=False)
        assert "loudnorm" not in graph

    def test_privacy_muffle_included(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30, privacy_mode=True)
        assert "lowpass=f=300" in graph

    def test_title_screen_gets_null_audio(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30)
        assert "anullsrc" in graph

    def test_loudnorm_not_applied_to_title_screens(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30, normalize_audio=True)
        # Title screen (a0) should use anullsrc, not loudnorm
        # Content clip (a1) should have loudnorm
        parts = graph.split(";")
        title_part = [p for p in parts if "[a0]" in p][0]
        content_part = [p for p in parts if "[a1]" in p][0]
        assert "loudnorm" not in title_part
        assert "loudnorm" in content_part

    def test_pre_extracted_audio_with_crossfade_uses_acrossfade(self, tmp_path: Path) -> None:
        """Pre-extracted audio with fade transitions must route through
        filter graph with acrossfade, not concat demuxer (which ignores
        crossfade overlap and causes audio drift)."""
        from unittest.mock import MagicMock, patch

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import extract_and_mix_audio

        wav_a = tmp_path / "clip_0_audio.wav"
        wav_b = tmp_path / "clip_1_audio.wav"
        wav_a.write_bytes(b"\x00" * 100)
        wav_b.write_bytes(b"\x00" * 100)

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        output = tmp_path / "audio.m4a"

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured_cmds.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "192000"
            result.stderr = ""
            return result

        # WHY: subprocess.run is the FFmpeg/ffprobe process boundary
        with patch(
            "immich_memories.processing.streaming_audio.subprocess.run",
            side_effect=fake_run,
        ):
            extract_and_mix_audio(
                clips=clips,
                transitions=["fade"],
                output_path=output,
                fade_duration=0.5,
                pre_extracted_audio=[wav_a, wav_b],
            )

        ffmpeg_cmds = [c for c in captured_cmds if c[0] == "ffmpeg"]
        assert ffmpeg_cmds, "Expected at least one FFmpeg command"

        main_cmd_str = " ".join(str(c) for c in ffmpeg_cmds[0])
        assert "acrossfade" in main_cmd_str, (
            "Expected acrossfade filter to handle crossfade overlap. "
            "Concat demuxer duplicates overlap audio causing drift."
        )

    def test_pre_extracted_audio_inputs_use_wav_paths(self, tmp_path: Path) -> None:
        """FFmpeg inputs should reference pre-extracted WAV files,
        not the original clip video paths."""
        from unittest.mock import MagicMock, patch

        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import extract_and_mix_audio

        wav_a = tmp_path / "clip_0_audio.wav"
        wav_b = tmp_path / "clip_1_audio.wav"
        wav_a.write_bytes(b"\x00" * 100)
        wav_b.write_bytes(b"\x00" * 100)

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0),
        ]
        output = tmp_path / "audio.m4a"

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured_cmds.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "192000"
            result.stderr = ""
            return result

        # WHY: subprocess.run is the FFmpeg/ffprobe process boundary
        with patch(
            "immich_memories.processing.streaming_audio.subprocess.run",
            side_effect=fake_run,
        ):
            extract_and_mix_audio(
                clips=clips,
                transitions=["fade"],
                output_path=output,
                fade_duration=0.5,
                pre_extracted_audio=[wav_a, wav_b],
            )

        ffmpeg_cmds = [c for c in captured_cmds if c[0] == "ffmpeg"]
        main_cmd_str = " ".join(str(c) for c in ffmpeg_cmds[0])
        assert str(wav_a) in main_cmd_str, "Expected WAV path as FFmpeg input"
        assert str(wav_b) in main_cmd_str, "Expected WAV path as FFmpeg input"

    def test_pre_extracted_audio_gets_frame_aligned_atrim(self) -> None:
        """Audio atrim must use frame-aligned duration (int(dur*fps)/fps),
        not raw clip.duration. Without this, int() truncation in the video
        frame count causes ~0.017s/clip drift → ~1.2s at 70 clips."""
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.017),
            AssemblyClip(path=Path("/b.mp4"), duration=4.517),
        ]
        graph = _build_audio_filter_graph(clips, ["fade"], 0.5, fps=30)
        # Frame-aligned: int(3.017*30)/30 = 90/30 = 3.0
        #                int(4.517*30)/30 = 135/30 = 4.5
        assert "atrim=0:3.0" in graph, f"Expected frame-aligned 3.0, got: {graph}"
        assert "atrim=0:4.5" in graph, f"Expected frame-aligned 4.5, got: {graph}"
        # NOT the raw clip.duration
        assert "atrim=0:3.017" not in graph
        assert "atrim=0:4.517" not in graph

    @requires_ffmpeg
    def test_loudnorm_does_not_eat_duration_at_scale(self, tmp_path: Path) -> None:
        """loudnorm in one-pass mode loses ~35ms/clip at filter boundaries.
        Over 30 clips this accumulates to >0.5s of drift — enough to detect.
        Regression test: atrim must come BEFORE loudnorm to clamp duration."""
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_audio import _build_audio_filter_graph

        n_clips = 30
        fps = 30
        clip_dur = 2.0
        fade_dur = 0.5
        frame_dur = int(clip_dur * fps) / fps

        # Create short WAV files
        wavs: list[Path] = []
        for i in range(n_clips):
            wav = tmp_path / f"clip_{i}.wav"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={300 + i * 40}:duration={clip_dur}",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(wav),
                ],
                capture_output=True,
                timeout=5,
            )
            wavs.append(wav)

        clips = [AssemblyClip(path=wavs[i], duration=clip_dur) for i in range(n_clips)]
        transitions = ["fade"] * (n_clips - 1)
        graph = _build_audio_filter_graph(clips, transitions, fade_dur, fps=fps)

        inputs: list[str] = []
        for wav in wavs:
            inputs.extend(["-i", str(wav)])

        out = tmp_path / "mixed.m4a"
        result = subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                *inputs,
                "-filter_complex",
                graph,
                "-map",
                "[aout]",
                "-c:a",
                "aac",
                str(out),
            ],
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[-300:]}"

        dur_result = subprocess.run(  # noqa: S603, S607
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        actual = float(dur_result.stdout.strip())
        expected = n_clips * frame_dur - (n_clips - 1) * fade_dur

        # WHY: loudnorm loses ~35ms/clip. At 30 clips that's ~1s.
        # With correct filter ordering (atrim before loudnorm), drift is <50ms.
        assert abs(actual - expected) < 0.05, (
            f"Audio duration drift: {actual:.3f}s vs expected {expected:.3f}s "
            f"(diff={actual - expected:+.3f}s). "
            f"loudnorm may be eating samples — check filter ordering."
        )


class TestMakeDecoderIntegration:
    """Verify _make_decoder wires clip metadata to FrameDecoder correctly."""

    def test_rotation_override_passed_through(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/clip.mp4"), duration=5.0, rotation_override=90)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30)

        assert decoder._rotation == 90
        assert "transpose=1" in decoder._build_vf()

    def test_privacy_mode_applied_to_non_title(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/clip.mp4"), duration=5.0)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30, privacy_mode=True)

        assert decoder._privacy_blur is True
        assert "gblur=sigma=37" in decoder._build_vf()

    def test_privacy_mode_not_applied_to_title_screen(self) -> None:
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import _make_decoder

        clip = AssemblyClip(path=Path("/title.mp4"), duration=3.0, is_title_screen=True)
        decoder = _make_decoder(clip, 0, 1920, 1080, 30, privacy_mode=True)

        assert decoder._privacy_blur is False
        assert "gblur" not in decoder._build_vf()


@requires_ffmpeg
class TestStreamingProgressCallback:
    """Verify streaming assembly reports per-frame progress during encoding."""

    def test_assemble_streaming_fires_progress(self, tmp_path: object) -> None:
        """assemble_streaming should call progress_callback with frame-level progress."""
        tmp = Path(str(tmp_path))
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        clip_path = tmp / "clip.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=10:duration=2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(clip_path),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        clips = [AssemblyClip(path=clip_path, duration=2.0, asset_id="test-0")]
        progress_calls: list[tuple[int, int]] = []

        output = tmp / "output.mp4"
        assemble_streaming(
            clips=clips,
            transitions=[],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            encoding_plan=_h264_plan(),
            progress_callback=lambda f, t: progress_calls.append((f, t)),
        )
        assert output.exists()
        assert len(progress_calls) > 0
        assert all(frame >= 0 for frame, _ in progress_calls)

    def test_full_fires_granular_progress(self, tmp_path: object) -> None:
        """streaming_assemble_full should fire more than 3 discrete events."""
        tmp = Path(str(tmp_path))
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import streaming_assemble_full

        clips = []
        for i in range(2):
            p = tmp / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=10:duration=2",
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency={440 + i * 220}:duration=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    str(p),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            clips.append(AssemblyClip(path=p, duration=2.0, asset_id=f"test-{i}"))

        progress_calls: list[tuple[float, str]] = []
        output = tmp / "final.mp4"
        streaming_assemble_full(
            clips=clips,
            transitions=["fade"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.3,
            encoding_plan=_h264_plan(),
            progress_callback=lambda p, m: progress_calls.append((p, m)),
        )
        assert output.exists()
        assert len(progress_calls) > 3
        intermediate = [pct for pct, _ in progress_calls if 0.05 < pct < 0.85]
        assert len(intermediate) > 0


@requires_ffmpeg
class TestFramePreviewCallback:
    def test_callback_fires_during_assembly(self, tmp_path: Path) -> None:
        """frame_preview_callback should fire at least once during a multi-clip assembly."""
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        captured: list[bytes] = []

        clips = []
        for i, color in enumerate(["red", "blue"]):
            clip_path = tmp_path / f"clip_{i}.mp4"
            subprocess.run(  # noqa: S603, S607
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:size=320x240:rate=10:duration=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo:d=3",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(clip_path),
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
            clips.append(AssemblyClip(path=clip_path, duration=3.0))

        output = tmp_path / "output.mp4"
        assemble_streaming(
            clips=clips,
            transitions=["fade"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
            fade_duration=0.5,
            frame_preview_callback=captured.append,
        )

        # At least 1 capture (first frame always fires since last_preview_time=0).
        # Fast encodes may not trigger the 2s interval again.
        assert len(captured) >= 1
        for jpeg in captured:
            assert jpeg[:2] == b"\xff\xd8"
            # Verify JPEG is decodable with correct dimensions
            from PIL import Image

            img = Image.open(__import__("io").BytesIO(jpeg))
            assert img.size == (320, 240)

    def test_no_callback_still_works(self, tmp_path: Path) -> None:
        """Assembly should work fine when frame_preview_callback is None."""
        from immich_memories.processing.assembly_config import AssemblyClip
        from immich_memories.processing.streaming_assembler import assemble_streaming

        clip_path = tmp_path / "clip.mp4"
        subprocess.run(  # noqa: S603, S607
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:size=320x240:rate=10:duration=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo:d=1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-shortest",
                str(clip_path),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        clip = AssemblyClip(path=clip_path, duration=1.0)

        output = tmp_path / "output.mp4"
        assemble_streaming(
            clips=[clip, clip],
            transitions=["fade"],
            output_path=output,
            width=320,
            height=240,
            fps=10,
        )
        assert output.exists()


def test_streaming_encoder_survives_noisy_ffmpeg_stderr(tmp_path: Path) -> None:
    """A chatty encoder must not deadlock the frame writer.

    FFmpeg <= 6.1 prints -stats/warnings from the transcode thread; once the 64 KB
    stderr pipe is full it stops reading stdin and the encode hangs forever.
    """
    import sys
    import threading

    from immich_memories.processing.streaming_assembler import StreamingEncoder

    fake_ffmpeg = tmp_path / "noisy_ffmpeg.py"
    fake_ffmpeg.write_text(
        "import sys\n"
        "sys.stderr.write('x' * 200_000)\n"  # > pipe capacity, before touching stdin
        "sys.stderr.flush()\n"
        "data = sys.stdin.buffer.read()\n"
        "open(sys.argv[-1], 'wb').write(b'fake' + len(data).to_bytes(4, 'big'))\n"
    )
    output = tmp_path / "out.mp4"
    encoder = StreamingEncoder(
        output,
        width=64,
        height=64,
        fps=1,
        encoding_plan=_h264_plan(),
        ffmpeg_command=(sys.executable, str(fake_ffmpeg)),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)  # 12 KB per frame

    def run() -> None:
        encoder.start()
        for _ in range(20):  # 240 KB > 64 KB stdin pipe
            encoder.write_frame(frame)
        encoder.finish()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=20)
    if worker.is_alive() and encoder._proc is not None:
        encoder._proc.kill()
    assert not worker.is_alive(), "frame writer deadlocked on an undrained stderr pipe"
    assert output.read_bytes().startswith(b"fake")
