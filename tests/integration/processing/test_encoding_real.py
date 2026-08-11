"""Integration tests for ClipEncoder with real FFmpeg.

Run with: make test-integration-processing
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _make_encoder(
    *, preserve_hdr: bool = False, default_resolution: tuple[int, int] = (1920, 1080)
):
    from immich_memories.processing.assembly_config import AssemblySettings
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_prober import FFmpegProber

    settings = AssemblySettings(preserve_hdr=preserve_hdr)
    prober = FFmpegProber(settings)
    return ClipEncoder(
        settings=settings,
        prober=prober,
        face_center_fn=lambda _path: None,
        default_resolution=default_resolution,
    )


def _make_clip(path: Path, duration: float = 3.0):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration)


# ---------------------------------------------------------------------------
# ClipEncoder.encode_single_clip
# ---------------------------------------------------------------------------


class TestEncodeSingleClip:
    def test_encode_720p_to_1080p(self, test_clip_720p: Path, tmp_path: Path):
        """Encoding a 720p clip with target 1080p produces correct resolution."""
        encoder = _make_encoder()
        clip = _make_clip(test_clip_720p, duration=3.0)
        out = tmp_path / "encoded_1080p.mp4"

        encoder.encode_single_clip(clip, out, target_resolution=(1920, 1080))

        assert out.exists()
        probe = ffprobe_json(out)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        # Verify output resolution
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        assert video_stream["width"] == 1920
        assert video_stream["height"] == 1080

    def test_encode_no_audio_clip_gets_silent_audio(self, no_audio_clip: Path, tmp_path: Path):
        """Encoding a video-only clip synthesizes a silent audio track."""
        encoder = _make_encoder()
        clip = _make_clip(no_audio_clip, duration=2.0)
        out = tmp_path / "encoded_silent.mp4"

        encoder.encode_single_clip(clip, out, target_resolution=(1280, 720))

        assert out.exists()
        probe = ffprobe_json(out)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio"), "Should synthesize audio for video-only input"


# ---------------------------------------------------------------------------
# ClipEncoder.trim_segment_copy
# ---------------------------------------------------------------------------


class TestTrimSegmentCopy:
    def test_trim_copy_produces_output(self, test_clip_720p: Path, tmp_path: Path):
        """Stream-copy trim produces a valid file with roughly correct duration."""
        encoder = _make_encoder()
        out = tmp_path / "trimmed_copy.mp4"

        encoder.trim_segment_copy(test_clip_720p, out, start=0.5, duration=1.5)

        assert out.exists()
        probe = ffprobe_json(out)
        assert has_stream(probe, "video")
        # WHY: stream copy trims at keyframes so duration is imprecise
        dur = get_duration(probe)
        assert 0.5 < dur < 3.5, f"Expected roughly 1.5s output, got {dur}"


# ---------------------------------------------------------------------------
# ClipEncoder.trim_segment_reencode
# ---------------------------------------------------------------------------


class TestTrimSegmentReencode:
    def test_reencode_produces_precise_duration(self, test_clip_720p: Path, tmp_path: Path):
        """Re-encode trim produces frame-accurate duration."""
        encoder = _make_encoder()
        out = tmp_path / "trimmed_reencode.mp4"

        encoder.trim_segment_reencode(test_clip_720p, out, start=0.5, duration=1.5)

        assert out.exists()
        probe = ffprobe_json(out)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        dur = get_duration(probe)
        assert abs(dur - 1.5) < 0.3, f"Expected ~1.5s, got {dur}"


# ---------------------------------------------------------------------------
# ClipEncoder.resolve_encode_resolution
# ---------------------------------------------------------------------------


class TestResolveEncodeResolution:
    def test_explicit_target(self):
        encoder = _make_encoder(default_resolution=(1920, 1080))
        assert encoder.resolve_encode_resolution((3840, 2160)) == (3840, 2160)

    def test_none_falls_back_to_default(self):
        encoder = _make_encoder(default_resolution=(1920, 1080))
        assert encoder.resolve_encode_resolution(None) == (1920, 1080)

    def test_settings_resolution_overrides_default(self):
        from immich_memories.processing.assembly_config import AssemblySettings
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = AssemblySettings(target_resolution=(1280, 720))
        prober = FFmpegProber(settings)
        encoder = ClipEncoder(
            settings=settings,
            prober=prober,
            face_center_fn=lambda _path: None,
            default_resolution=(1920, 1080),
        )
        # settings.target_resolution takes priority over default_resolution
        assert encoder.resolve_encode_resolution(None) == (1280, 720)


# ---------------------------------------------------------------------------
# Final artifact validation and publication
# ---------------------------------------------------------------------------


def _write_tiny_sdr_video(
    output_path: Path,
    codec_args: list[str],
    *,
    duration_seconds: float = 0.3,
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:size=160x90:rate=10:duration={duration_seconds}",
            "-vf",
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            *codec_args,
            "-an",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _write_tiny_hlg_hevc(output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=160x90:rate=10:duration=0.3",
            "-vf",
            (
                "format=yuv420p10le,"
                "setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67"
            ),
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-x265-params",
            "log-level=error",
            "-pix_fmt",
            "yuv420p10le",
            "-tag:v",
            "hvc1",
            "-an",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


class TestValidatedOutputPublication:
    def test_real_h264_output_is_probed_and_atomically_published(self, tmp_path: Path):
        from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
        from immich_memories.processing.output_contract import publish_validated_output

        staged = tmp_path / "memory.assembling.mp4"
        final = tmp_path / "memory.mp4"
        _write_tiny_sdr_video(
            staged,
            ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
        )
        plan = EncodingPlan(
            codec=OutputCodec.H264,
            encoder="libx264",
            encoder_args=("-c:v", "libx264"),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv420p",
            container="mp4",
        )

        probe = publish_validated_output(staged, final, plan)

        assert final.exists()
        assert not staged.exists()
        assert probe.codec == "h264"
        assert (probe.width, probe.height) == (160, 90)
        assert probe.duration_seconds > 0

    def test_real_prores_output_uses_the_ffprobe_codec_name(self, tmp_path: Path):
        from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
        from immich_memories.processing.output_contract import publish_validated_output

        staged = tmp_path / "memory.assembling.mov"
        final = tmp_path / "memory.mov"
        _write_tiny_sdr_video(
            staged,
            ["-c:v", "prores_ks", "-profile:v", "1", "-pix_fmt", "yuv422p10le"],
        )
        plan = EncodingPlan(
            codec=OutputCodec.PRORES,
            encoder="prores_ks",
            encoder_args=("-c:v", "prores_ks"),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv422p10le",
            container="mov",
        )

        probe = publish_validated_output(staged, final, plan)

        assert final.exists()
        assert probe.codec == "prores"
        assert probe.container == "mov"

    def test_real_valid_then_truncated_faststart_output_is_rejected(self, tmp_path: Path):
        from immich_memories.processing.encoding_plan import (
            EncodingPlan,
            HdrTransfer,
            OutputCodec,
        )
        from immich_memories.processing.output_contract import (
            InvalidOutputArtifact,
            validate_output,
        )

        valid = tmp_path / "valid.mp4"
        truncated = tmp_path / "truncated.mp4"
        _write_tiny_sdr_video(
            valid,
            ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
            duration_seconds=2.0,
        )
        plan = EncodingPlan(
            codec=OutputCodec.H264,
            encoder="libx264",
            encoder_args=("-c:v", "libx264"),
            target_transfer=HdrTransfer.NONE,
            tone_map_to_sdr=False,
            pixel_format="yuv420p",
            container="mp4",
        )

        valid_probe = validate_output(valid, plan)
        shutil.copyfile(valid, truncated)
        intact_bytes = truncated.read_bytes()
        truncated.write_bytes(intact_bytes[: len(intact_bytes) * 3 // 4])

        assert valid_probe.decoded_frames == 20
        with pytest.raises(InvalidOutputArtifact, match="decode errors"):
            validate_output(truncated, plan)

    def test_real_hevc_10_bit_decode_matches_p010_encoder_input(self, tmp_path: Path):
        from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
        from immich_memories.processing.output_contract import validate_output

        output = tmp_path / "memory.mp4"
        _write_tiny_hlg_hevc(output)
        plan = EncodingPlan(
            codec=OutputCodec.H265,
            encoder="hevc_videotoolbox",
            encoder_args=("-c:v", "hevc_videotoolbox"),
            target_transfer=HdrTransfer.HLG,
            tone_map_to_sdr=False,
            pixel_format="p010le",
            container="mp4",
        )

        probe = validate_output(output, plan)

        assert probe.pixel_format == "yuv420p10le"
        assert probe.decoded_frames == 3
