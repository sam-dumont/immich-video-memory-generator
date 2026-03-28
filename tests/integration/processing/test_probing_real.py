"""Integration tests for FFmpegProber and clip_probing with real FFmpeg.

Run with: make test-integration-processing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _make_prober():
    from immich_memories.processing.assembly_config import AssemblySettings
    from immich_memories.processing.ffmpeg_prober import FFmpegProber

    return FFmpegProber(AssemblySettings())


def _make_clip(path: Path, duration: float = 3.0):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration)


# ---------------------------------------------------------------------------
# FFmpegProber.get_video_resolution
# ---------------------------------------------------------------------------


class TestGetVideoResolution:
    def test_landscape_720p(self, test_clip_720p: Path):
        prober = _make_prober()
        res = prober.get_video_resolution(test_clip_720p)
        assert res == (1280, 720)

    def test_portrait_resolution(self, portrait_clip: Path):
        prober = _make_prober()
        res = prober.get_video_resolution(portrait_clip)
        assert res is not None
        w, h = res
        assert h > w, f"Portrait clip should be taller than wide, got {w}x{h}"

    def test_small_resolution(self, short_clip: Path):
        prober = _make_prober()
        res = prober.get_video_resolution(short_clip)
        assert res == (640, 480)

    def test_nonexistent_file(self, tmp_path: Path):
        prober = _make_prober()
        res = prober.get_video_resolution(tmp_path / "nonexistent.mp4")
        assert res is None


# ---------------------------------------------------------------------------
# FFmpegProber.probe_duration
# ---------------------------------------------------------------------------


class TestProbeDuration:
    def test_audio_duration(self, test_clip_720p: Path):
        prober = _make_prober()
        dur = prober.probe_duration(test_clip_720p, stream_type="audio")
        assert 2.5 < dur < 3.5, f"Expected ~3s audio duration, got {dur}"

    def test_video_duration(self, test_clip_720p: Path):
        prober = _make_prober()
        dur = prober.probe_duration(test_clip_720p, stream_type="video")
        assert 2.5 < dur < 3.5, f"Expected ~3s video duration, got {dur}"

    def test_no_audio_clip_falls_back_to_format(self, no_audio_clip: Path):
        """Probing audio duration on a video-only file falls back to format duration."""
        prober = _make_prober()
        dur = prober.probe_duration(no_audio_clip, stream_type="audio")
        # Should get format duration as fallback, not 0
        assert dur > 0, "Should fall back to format duration for video-only files"

    def test_short_clip_duration(self, short_clip: Path):
        prober = _make_prober()
        dur = prober.probe_duration(short_clip, stream_type="video")
        assert 0.5 < dur < 1.5, f"Expected ~1s duration, got {dur}"


# ---------------------------------------------------------------------------
# FFmpegProber.probe_framerate
# ---------------------------------------------------------------------------


class TestProbeFramerate:
    def test_30fps_clip(self, test_clip_720p: Path):
        prober = _make_prober()
        fps = prober.probe_framerate(test_clip_720p)
        assert abs(fps - 30.0) < 1.0, f"Expected ~30fps, got {fps}"

    def test_24fps_clip(self, short_clip: Path):
        prober = _make_prober()
        fps = prober.probe_framerate(short_clip)
        assert abs(fps - 24.0) < 1.0, f"Expected ~24fps, got {fps}"


# ---------------------------------------------------------------------------
# FFmpegProber.has_audio_stream / has_video_stream
# ---------------------------------------------------------------------------


class TestStreamDetection:
    def test_has_audio_true(self, test_clip_720p: Path):
        prober = _make_prober()
        assert prober.has_audio_stream(test_clip_720p) is True

    def test_has_audio_false(self, no_audio_clip: Path):
        prober = _make_prober()
        assert prober.has_audio_stream(no_audio_clip) is False

    def test_has_video_true(self, test_clip_720p: Path):
        prober = _make_prober()
        assert prober.has_video_stream(test_clip_720p) is True

    def test_has_video_on_no_audio_clip(self, no_audio_clip: Path):
        prober = _make_prober()
        assert prober.has_video_stream(no_audio_clip) is True


# ---------------------------------------------------------------------------
# FFmpegProber.detect_best_resolution
# ---------------------------------------------------------------------------


class TestDetectBestResolution:
    def test_single_landscape_clip(self, test_clip_720p: Path):
        prober = _make_prober()
        clips = [_make_clip(test_clip_720p)]
        w, h = prober.detect_best_resolution(clips)
        assert w >= h, "Single landscape clip should produce landscape resolution"

    def test_single_portrait_clip(self, portrait_clip: Path):
        prober = _make_prober()
        clips = [_make_clip(portrait_clip, duration=2.0)]
        w, h = prober.detect_best_resolution(clips)
        assert h >= w, "Single portrait clip should produce portrait resolution"

    def test_empty_clips_defaults_1080p(self):
        prober = _make_prober()
        res = prober.detect_best_resolution([])
        assert res == (1920, 1080)


# ---------------------------------------------------------------------------
# FFmpegProber.probe_batch_durations
# ---------------------------------------------------------------------------


class TestProbeBatchDurations:
    def test_two_clips(self, test_clip_720p: Path, test_clip_720p_b: Path):
        prober = _make_prober()
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        audio_durs, video_durs = prober.probe_batch_durations(clips)
        assert len(audio_durs) == 2
        assert len(video_durs) == 2
        for dur in audio_durs + video_durs:
            assert dur > 0, "All durations should be positive"


# ---------------------------------------------------------------------------
# clip_probing: get_video_duration, get_video_info, get_main_video_stream_map
# ---------------------------------------------------------------------------


class TestClipProbing:
    def test_get_video_duration(self, test_clip_720p: Path):
        from immich_memories.processing.clip_probing import get_video_duration

        dur = get_video_duration(test_clip_720p)
        assert 2.5 < dur < 3.5, f"Expected ~3s, got {dur}"

    def test_get_video_info_has_expected_keys(self, test_clip_720p: Path):
        from immich_memories.processing.clip_probing import get_video_info

        info = get_video_info(test_clip_720p)
        assert info["width"] == 1280
        assert info["height"] == 720
        assert abs(info["fps"] - 30.0) < 1.0
        assert info["codec"] == "h264"
        assert info["duration"] > 0

    def test_get_video_info_portrait(self, portrait_clip: Path):
        from immich_memories.processing.clip_probing import get_video_info

        info = get_video_info(portrait_clip)
        assert info["width"] == 720
        assert info["height"] == 1280

    def test_get_main_video_stream_map(self, test_clip_720p: Path):
        from immich_memories.processing.clip_probing import get_main_video_stream_map

        stream_map = get_main_video_stream_map(test_clip_720p)
        # Single-stream file should return default
        assert stream_map == "0:v:0"

    def test_get_video_info_short_clip(self, short_clip: Path):
        from immich_memories.processing.clip_probing import get_video_info

        info = get_video_info(short_clip)
        assert info["width"] == 640
        assert info["height"] == 480
        assert abs(info["fps"] - 24.0) < 1.0


# ---------------------------------------------------------------------------
# FFmpegProber.detect_framerate and detect_max_framerate
# ---------------------------------------------------------------------------


class TestDetectFramerate:
    def test_detect_framerate(self, test_clip_720p: Path):
        prober = _make_prober()
        fps = prober.detect_framerate(test_clip_720p)
        assert fps is not None
        assert abs(fps - 30.0) < 1.0

    def test_detect_max_framerate_rounds_to_30(self, test_clip_720p: Path):
        prober = _make_prober()
        clips = [_make_clip(test_clip_720p)]
        max_fps = prober.detect_max_framerate(clips)
        assert max_fps == 30


# ---------------------------------------------------------------------------
# FFmpegProber.estimate_duration
# ---------------------------------------------------------------------------


class TestEstimateDuration:
    def test_no_clips(self):
        prober = _make_prober()
        assert prober.estimate_duration([]) == 0.0

    def test_single_clip(self, test_clip_720p: Path):
        prober = _make_prober()
        clips = [_make_clip(test_clip_720p, duration=3.0)]
        est = prober.estimate_duration(clips)
        assert est == pytest.approx(3.0)

    def test_two_clips_with_crossfade(self, test_clip_720p: Path):
        from immich_memories.processing.assembly_config import (
            AssemblySettings,
            TransitionType,
        )
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = AssemblySettings(
            transition=TransitionType.CROSSFADE,
            transition_duration=0.5,
        )
        prober = FFmpegProber(settings)
        clips = [_make_clip(test_clip_720p, 3.0), _make_clip(test_clip_720p, 3.0)]
        est = prober.estimate_duration(clips)
        # 3 + 3 - 0.5 = 5.5
        assert est == pytest.approx(5.5)


# ---------------------------------------------------------------------------
# FFmpegProber.parse_fps_str (static)
# ---------------------------------------------------------------------------


class TestParseFpsStr:
    def test_fraction(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        assert FFmpegProber.parse_fps_str("30/1") == pytest.approx(30.0)

    def test_ntsc(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        assert FFmpegProber.parse_fps_str("60000/1001") == pytest.approx(59.94, abs=0.01)

    def test_plain_number(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        assert FFmpegProber.parse_fps_str("24") == pytest.approx(24.0)

    def test_empty(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        assert FFmpegProber.parse_fps_str("") is None
