"""Integration tests for ConcatService (filter graph assembly) with real FFmpeg.

Tests assemble_batch_direct with 2-clip and 3-clip inputs, verifying
that the filter graph produces valid video output with expected duration.

Run with: make test-integration-processing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _make_settings(**overrides):
    from immich_memories.processing.assembly_config import AssemblySettings, TransitionType

    defaults = {
        "transition": TransitionType.CROSSFADE,
        "transition_duration": 0.3,
        "output_crf": 28,
        "preserve_hdr": False,
        "auto_resolution": False,
        "target_resolution": (1280, 720),
        "normalize_clip_audio": False,
    }
    defaults.update(overrides)
    return AssemblySettings(**defaults)


def _make_clip(path: Path, duration: float = 3.0):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration)


def _noop_face_center(_path: Path) -> tuple[float, float] | None:
    return None


def _build_concat_service(settings=None):
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_filter_graph import ConcatService
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.filter_builder import FilterBuilder

    settings = settings or _make_settings()
    prober = FFmpegProber(settings)
    encoder = ClipEncoder(settings, prober, _noop_face_center)
    fb = FilterBuilder(settings, prober, _noop_face_center)
    return ConcatService(settings, prober, encoder, fb)


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


# ---------------------------------------------------------------------------
# ConcatService.assemble_batch_direct
# ---------------------------------------------------------------------------


class TestConcatServiceTwoClips:
    def test_two_clips_produce_valid_output(
        self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path
    ):
        svc = _build_concat_service()
        output = tmp_path / "concat_2.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = svc.assemble_batch_direct(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

    def test_two_clips_duration(self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path):
        """Output duration is roughly (clip_a + clip_b - crossfade)."""
        svc = _build_concat_service()
        output = tmp_path / "concat_2_dur.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        svc.assemble_batch_direct(clips, output)

        probe = ffprobe_json(output)
        duration = get_duration(probe)
        # 3 + 3 - 0.3 crossfade = ~5.7, allow generous tolerance
        assert 4.5 < duration < 7.5, f"Expected ~5.7s, got {duration}"

    def test_two_clips_resolution(
        self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path
    ):
        svc = _build_concat_service()
        output = tmp_path / "concat_2_res.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        svc.assemble_batch_direct(clips, output)

        probe = ffprobe_json(output)
        w, h = _get_resolution(probe)
        assert w == 1280
        assert h == 720


class TestConcatServiceThreeClips:
    def test_three_clips_produce_valid_output(
        self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path
    ):
        svc = _build_concat_service()
        output = tmp_path / "concat_3.mp4"
        clips = [
            _make_clip(test_clip_720p),
            _make_clip(test_clip_720p_b),
            _make_clip(test_clip_720p),
        ]
        result = svc.assemble_batch_direct(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

    def test_three_clips_duration(
        self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path
    ):
        """Three clips with crossfade: ~(3+3+3 - 2*0.3) = 8.4s."""
        svc = _build_concat_service()
        output = tmp_path / "concat_3_dur.mp4"
        clips = [
            _make_clip(test_clip_720p),
            _make_clip(test_clip_720p_b),
            _make_clip(test_clip_720p),
        ]
        svc.assemble_batch_direct(clips, output)

        probe = ffprobe_json(output)
        duration = get_duration(probe)
        assert 6.5 < duration < 10.5, f"Expected ~8.4s, got {duration}"


class TestConcatServiceSingleClip:
    def test_single_clip_passthrough(self, test_clip_720p: Path, tmp_path: Path):
        """Single clip is copied, not assembled via filter graph."""
        svc = _build_concat_service()
        output = tmp_path / "concat_1.mp4"
        clips = [_make_clip(test_clip_720p)]
        result = svc.assemble_batch_direct(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert 2.5 < duration < 4.0

    def test_empty_clips_raises(self):
        svc = _build_concat_service()
        with pytest.raises(ValueError, match="No clips"):
            svc.assemble_batch_direct([], Path("/tmp/dummy.mp4"))


class TestConcatServiceMergeIntermediate:
    def test_merge_two_batches(self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path):
        """merge_intermediate_batches concatenates pre-encoded batch files."""
        svc = _build_concat_service()
        output = tmp_path / "merged.mp4"
        batches = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = svc.merge_intermediate_batches(batches, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        assert duration > 4.0

    def test_merge_single_batch_copies(self, test_clip_720p: Path, tmp_path: Path):
        """Single batch is copied without FFmpeg."""
        svc = _build_concat_service()
        output = tmp_path / "single_merge.mp4"
        batches = [_make_clip(test_clip_720p)]
        result = svc.merge_intermediate_batches(batches, output)

        assert result.exists()
        assert result.stat().st_size > 0
