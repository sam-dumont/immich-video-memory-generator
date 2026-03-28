"""Integration tests for ClipEncoder with real FFmpeg.

Run with: make test-integration-processing
"""

from __future__ import annotations

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
