"""Integration tests for the assembly core: engine, encoder, filter_builder, title_inserter, hdr.

All tests use REAL FFmpeg with small synthetic clips. No mocks.
Run with: make test-integration-assembly
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    """Extract (width, height) from the first video stream."""
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _make_settings(**overrides):
    """Create AssemblySettings with sensible test defaults (no config fallback)."""
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


def _make_clip(path: Path, duration: float = 3.0, **kwargs):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration, **kwargs)


def _make_prober(settings=None):
    from immich_memories.processing.ffmpeg_prober import FFmpegProber

    return FFmpegProber(settings or _make_settings())


def _noop_face_center(_path: Path) -> tuple[float, float] | None:
    return None


def _noop_cancel() -> None:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_clip_portrait(fixtures_dir) -> Path:
    """3-second 720x1280 portrait clip."""
    out = fixtures_dir / "test_portrait.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=720x1280:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
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
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return out


# ===================================================================
# assembly_engine.py
# ===================================================================


class TestAssemblyEngineScalable:
    """Tests for AssemblyEngine.assemble_scalable — the main pipeline."""

    def test_assemble_two_clips_crossfade(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Two clips with crossfade produce valid output with duration < sum of inputs."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "two_clips.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_scalable(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        # Two 3s clips with 0.3s crossfade -> ~5.7s, allow generous tolerance
        assert 4.5 < duration < 7.0

    def test_assemble_single_clip_passthrough(self, test_clip_720p, tmp_path):
        """Single clip should be copied through (passthrough)."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "single.mp4"
        clips = [_make_clip(test_clip_720p)]
        result = engine.assemble_scalable(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert 2.5 < duration < 4.0

    def test_assemble_with_resolution_override(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Setting target_resolution=720p produces 720p output."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(target_resolution=(1280, 720))
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "res_override.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_scalable(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        w, h = _get_resolution(probe)
        assert w == 1280
        assert h == 720

    def test_assemble_no_clips_raises(self):
        """Empty clip list raises ValueError."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        with pytest.raises(ValueError, match="No clips"):
            engine.assemble_scalable([], Path("/tmp/out.mp4"))

    def test_assemble_with_cut_transitions(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Pre-decided cut transitions produce output with full combined duration."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(
            transition=TransitionType.CUT,
            predecided_transitions=["cut"],
        )
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "cuts.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_scalable(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        duration = get_duration(probe)
        # Cuts: no overlap, so ~6s total
        assert 5.0 < duration < 7.5


# ===================================================================
# clip_encoder.py
# ===================================================================


class TestClipEncoder:
    """Tests for ClipEncoder.encode_single_clip."""

    def test_encode_clip_default(self, test_clip_720p, tmp_path):
        """Encode a clip with default settings produces valid output."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "encoded.mp4"
        clip = _make_clip(test_clip_720p)
        encoder.encode_single_clip(clip, output, target_resolution=(1280, 720))

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        assert 2.5 < duration < 4.0

    def test_encode_clip_with_rotation(self, test_clip_720p, tmp_path):
        """Encoding with 90 rotation swaps dimensions."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "rotated.mp4"
        clip = _make_clip(test_clip_720p, rotation_override=90)
        # WHY: target_resolution is WxH before rotation; after 90 rotation
        # the output should be rotated (720x1280)
        encoder.encode_single_clip(clip, output, target_resolution=(1280, 720))

        assert output.exists()
        probe = ffprobe_json(output)
        w, h = _get_resolution(probe)
        # After 90 rotation of a 1280x720 source with 1280x720 target,
        # the rotated source becomes 720x1280, then scaled to fit 1280x720
        # with black bars. So output is still 1280x720 but content is letter/pillarboxed.
        assert w == 1280
        assert h == 720

    def test_encode_clip_resolution(self, test_clip_720p, tmp_path):
        """Encoding at 720p target produces 720p output."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "res.mp4"
        clip = _make_clip(test_clip_720p)
        encoder.encode_single_clip(clip, output, target_resolution=(1280, 720))

        probe = ffprobe_json(output)
        w, h = _get_resolution(probe)
        assert w == 1280
        assert h == 720

    def test_encode_clip_blur_mode(self, test_clip_portrait, tmp_path):
        """Portrait clip encoded with blur mode to landscape target produces valid output."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(scale_mode="blur")
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "blur.mp4"
        clip = _make_clip(test_clip_portrait)
        encoder.encode_single_clip(clip, output, target_resolution=(1280, 720))

        assert output.exists()
        probe = ffprobe_json(output)
        w, h = _get_resolution(probe)
        assert w == 1280
        assert h == 720

    def test_trim_segment_copy(self, test_clip_720p, tmp_path):
        """Trim a segment using stream copy."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "trimmed.mp4"
        encoder.trim_segment_copy(test_clip_720p, output, start=0.5, duration=1.5)

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # Stream copy trim is not frame-accurate, but should be roughly correct
        assert 0.5 < duration < 3.0


# ===================================================================
# filter_builder.py
# ===================================================================


class TestFilterBuilder:
    """Tests for FilterBuilder: building filter chains that FFmpeg can parse."""

    def test_build_clip_video_filter(self, test_clip_720p):
        """build_clip_video_filter produces a parseable filter string."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        clip = _make_clip(test_clip_720p)
        result = fb.build_clip_video_filter(0, clip, ctx)

        assert "[0:v]" in result
        assert "scale=1280:720" in result
        assert "[v0scaled]" in result

    def test_build_xfade_chain(self, test_clip_720p, test_clip_720p_b):
        """build_xfade_chain for 2 clips produces xfade filter parts."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None, None],
            clip_primaries=[None, None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        audio_labels = ["[a0prep]", "[a1prep]"]

        parts, final_video, final_audio, total_dur = fb.build_xfade_chain(clips, ctx, audio_labels)

        assert len(parts) > 0
        xfade_found = any("xfade" in p for p in parts)
        assert xfade_found, f"Expected xfade in filter parts: {parts}"
        assert final_video  # non-empty label
        assert final_audio  # non-empty label

    def test_build_audio_prep_filters(self, test_clip_720p, test_clip_720p_b):
        """build_audio_prep_filters produces filter parts and labels for each clip."""
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        parts, labels = fb.build_audio_prep_filters(clips)

        assert len(labels) == 2
        assert labels[0] == "[a0prep]"
        assert labels[1] == "[a1prep]"
        assert len(parts) == 2

    def test_build_smart_transition_chain(self, test_clip_720p, test_clip_720p_b):
        """build_smart_transition_chain with mixed transitions produces valid filter."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None, None],
            clip_primaries=[None, None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        audio_labels = ["[a0prep]", "[a1prep]"]
        transitions = ["fade"]

        parts, final_video, final_audio = fb.build_smart_transition_chain(
            clips, transitions, ctx, audio_labels
        )

        assert len(parts) > 0
        assert final_video
        assert final_audio

    def test_xfade_chain_runs_through_ffmpeg(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """End-to-end: filter chain from FilterBuilder actually works in FFmpeg."""
        from immich_memories.processing.assembly_engine import create_assembly_context
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        ctx = create_assembly_context(settings, prober, clips, 1280, 720)

        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        filter_parts = [fb.build_clip_video_filter(i, clip, ctx) for i, clip in enumerate(clips)]
        audio_parts, audio_labels = fb.build_audio_prep_filters(clips)
        filter_parts.extend(audio_parts)
        xfade_parts, final_video, final_audio, _ = fb.build_xfade_chain(clips, ctx, audio_labels)
        filter_parts.extend(xfade_parts)

        output = tmp_path / "filter_e2e.mp4"
        result = encoder.run_ffmpeg_assembly(
            inputs,
            ";".join(filter_parts),
            final_video,
            final_audio,
            output,
            clips,
            ctx,
        )

        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[-500:]}"
        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")


# ===================================================================
# title_inserter.py
# ===================================================================


class TestTitleInserter:
    """Tests for TitleInserter date parsing, month detection, and divider building."""

    def test_parse_clip_date_iso(self, test_clip_720p):
        """parse_clip_date handles ISO date format."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clip = _make_clip(test_clip_720p, date="2024-06-15")
        result = ti.parse_clip_date(clip)
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15

    def test_parse_clip_date_datetime(self, test_clip_720p):
        """parse_clip_date handles datetime format."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clip = _make_clip(test_clip_720p, date="2024-06-15T14:30:00")
        result = ti.parse_clip_date(clip)
        assert result is not None
        assert result.year == 2024
        assert result.month == 6

    def test_parse_clip_date_none(self, test_clip_720p):
        """parse_clip_date returns None for missing date."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clip = _make_clip(test_clip_720p, date=None)
        assert ti.parse_clip_date(clip) is None

    def test_detect_month_changes(self, test_clip_720p, test_clip_720p_b):
        """detect_month_changes finds transitions between different months."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p, date="2024-01-20"),
            _make_clip(test_clip_720p_b, date="2024-03-10"),
        ]
        changes = ti.detect_month_changes(clips)

        # First clip starts Jan, then March = 2 month groups
        assert len(changes) == 2
        assert changes[0] == (0, 1, 2024)  # Jan starts at index 0
        assert changes[1] == (2, 3, 2024)  # Mar starts at index 2

    def test_detect_year_changes(self, test_clip_720p, test_clip_720p_b):
        """detect_year_changes finds year transitions."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p_b, date="2024-01-05"),
        ]
        changes = ti.detect_year_changes(clips)

        assert len(changes) == 2
        assert changes[0] == (0, 2023)
        assert changes[1] == (1, 2024)

    def test_build_clips_with_dividers(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """build_clips_with_dividers inserts month divider clips at boundaries."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        # Use a real clip as a stand-in for a divider video
        divider_path = test_clip_720p_b
        month_divider_paths = {
            (2024, 1): divider_path,
            (2024, 3): divider_path,
        }
        title_settings = TitleScreenSettings(
            show_month_dividers=True,
            month_divider_duration=2.0,
        )

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p, date="2024-01-20"),
            _make_clip(test_clip_720p_b, date="2024-03-10"),
        ]

        result = ti.build_clips_with_dividers(clips, month_divider_paths, title_settings)

        # 2 dividers (Jan, Mar) + 3 original clips = 5
        assert len(result) == 5
        assert result[0].is_title_screen  # Jan divider
        assert not result[1].is_title_screen  # Jan clip
        assert not result[2].is_title_screen  # Jan clip
        assert result[3].is_title_screen  # Mar divider
        assert not result[4].is_title_screen  # Mar clip

    def test_get_orientation_from_clips(self, test_clip_720p, test_clip_portrait):
        """get_orientation_from_clips detects landscape vs portrait majority."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        # Majority landscape
        landscape_clips = [_make_clip(test_clip_720p)] * 3
        assert ti.get_orientation_from_clips(landscape_clips) == "landscape"

        # Majority portrait
        portrait_clips = [_make_clip(test_clip_portrait)] * 3
        assert ti.get_orientation_from_clips(portrait_clips) == "portrait"

    def test_get_resolution_tier(self, test_clip_720p):
        """get_resolution_tier returns correct tier for 1280x720 clips."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [_make_clip(test_clip_720p)]
        tier = ti.get_resolution_tier(clips)
        # 1280x720 -> max_dim=1280 >= 1080 -> classified as 1080p tier
        assert tier == "1080p"


# ===================================================================
# hdr_utilities.py
# ===================================================================


class TestHDRUtilities:
    """Tests for HDR detection and colorspace utilities."""

    def test_detect_hdr_on_sdr_clip(self, test_clip_720p):
        """SDR test clip should return None for HDR type."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        result = _detect_hdr_type(test_clip_720p)
        assert result is None

    def test_detect_color_primaries(self, test_clip_720p):
        """detect_color_primaries returns a string or None for test clip."""
        from immich_memories.processing.hdr_utilities import _detect_color_primaries

        result = _detect_color_primaries(test_clip_720p)
        # Synthetic testsrc2 clips may or may not report primaries
        assert result is None or isinstance(result, str)

    def test_get_colorspace_filter_hlg(self):
        """HLG colorspace filter contains arib-std-b67."""
        from immich_memories.processing.hdr_utilities import _get_colorspace_filter

        result = _get_colorspace_filter("hlg")
        assert "arib-std-b67" in result
        assert "setparams" in result

    def test_get_colorspace_filter_pq(self):
        """PQ colorspace filter contains smpte2084."""
        from immich_memories.processing.hdr_utilities import _get_colorspace_filter

        result = _get_colorspace_filter("pq")
        assert "smpte2084" in result
        assert "setparams" in result

    def test_get_dominant_hdr_type_sdr_clips(self, test_clip_720p, test_clip_720p_b):
        """All-SDR clips default to hlg."""
        from immich_memories.processing.hdr_utilities import _get_dominant_hdr_type

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = _get_dominant_hdr_type(clips)
        assert result == "hlg"

    def test_get_clip_hdr_types(self, test_clip_720p, test_clip_720p_b):
        """SDR clips all return None HDR type."""
        from immich_memories.processing.hdr_utilities import _get_clip_hdr_types

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = _get_clip_hdr_types(clips)
        assert len(result) == 2
        assert all(t is None for t in result)

    def test_has_any_hdr_clip_false_for_sdr(self, test_clip_720p):
        """SDR-only clips return False for has_any_hdr_clip."""
        from immich_memories.processing.hdr_utilities import has_any_hdr_clip

        clips = [_make_clip(test_clip_720p)]
        assert has_any_hdr_clip(clips) is False

    def test_get_gpu_encoder_args_returns_list(self):
        """_get_gpu_encoder_args returns a non-empty list of strings."""
        from immich_memories.processing.hdr_utilities import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(crf=23, preserve_hdr=False)
        assert isinstance(args, list)
        assert len(args) > 0
        assert all(isinstance(a, str) for a in args)

    def test_get_gpu_encoder_args_hdr(self):
        """_get_gpu_encoder_args with preserve_hdr includes HDR flags."""
        from immich_memories.processing.hdr_utilities import _get_gpu_encoder_args

        args = _get_gpu_encoder_args(crf=18, preserve_hdr=True, hdr_type="hlg")
        args_str = " ".join(args)
        # Should include HEVC/x265 and color metadata
        assert "hevc" in args_str or "libx265" in args_str

    def test_get_hdr_conversion_filter_same_type(self):
        """Same source and target HDR type returns empty string."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        result = _get_hdr_conversion_filter("hlg", "hlg")
        assert result == ""

    def test_get_hdr_conversion_filter_sdr_to_hlg(self):
        """SDR to HLG conversion returns a filter (may be empty if no zscale)."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        result = _get_hdr_conversion_filter(None, "hlg")
        # Result depends on zscale availability, but should not crash
        assert isinstance(result, str)


# ===================================================================
# assembly_engine.py — context building
# ===================================================================


class TestAssemblyContext:
    """Tests for create_assembly_context and resolve_target_resolution."""

    def test_create_assembly_context_sdr(self, test_clip_720p):
        """SDR context has yuv420p pixel format and empty colorspace filter."""
        from immich_memories.processing.assembly_engine import create_assembly_context

        settings = _make_settings(preserve_hdr=False)
        prober = _make_prober(settings)
        clips = [_make_clip(test_clip_720p)]

        ctx = create_assembly_context(settings, prober, clips, 1280, 720)

        assert ctx.target_w == 1280
        assert ctx.target_h == 720
        assert ctx.pix_fmt == "yuv420p"
        assert ctx.colorspace_filter == ""

    def test_resolve_target_resolution_explicit(self, test_clip_720p):
        """Explicit target_resolution is used directly."""
        from immich_memories.processing.assembly_engine import resolve_target_resolution

        settings = _make_settings(target_resolution=(1920, 1080))
        prober = _make_prober(settings)
        clips = [_make_clip(test_clip_720p)]

        w, h = resolve_target_resolution(settings, prober, clips)
        assert w == 1920
        assert h == 1080

    def test_resolve_target_resolution_auto(self, test_clip_720p, test_clip_720p_b):
        """Auto-resolution detects resolution from clips."""
        from immich_memories.processing.assembly_engine import resolve_target_resolution

        settings = _make_settings(auto_resolution=True, target_resolution=None)
        prober = _make_prober(settings)
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]

        w, h = resolve_target_resolution(settings, prober, clips)
        # 1280x720 clips -> max_dim=1280 >= 1080 -> auto-detected as 1080p
        assert w == 1920
        assert h == 1080


# ===================================================================
# assembly_engine.py — transition logic
# ===================================================================


class TestTransitionDecisions:
    """Tests for get_transition_types and decide_transitions."""

    def test_get_transition_types_crossfade(self, test_clip_720p, test_clip_720p_b):
        """CROSSFADE setting produces all fade transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition=TransitionType.CROSSFADE)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["fade"]

    def test_get_transition_types_cut(self, test_clip_720p, test_clip_720p_b):
        """CUT setting produces all cut transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition=TransitionType.CUT)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["cut"]

    def test_title_screen_forces_fade(self, test_clip_720p, test_clip_720p_b):
        """Title screen clips always get fade transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition=TransitionType.CUT)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        clips = [
            _make_clip(test_clip_720p, is_title_screen=True),
            _make_clip(test_clip_720p_b),
        ]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["fade"]

    def test_validate_fade_transitions_short_clips(self):
        """Short clips get downgraded from fade to cut."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition_duration=0.5)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        transitions = ["fade"]
        # Clip durations too short for 0.5s fade (min = 1.0s needed)
        clip_durations = [0.5, 0.5]

        result = engine._validate_fade_transitions(transitions, clip_durations, 0.5)
        assert result == ["cut"]

    def test_decide_transitions_two_clips(self, test_clip_720p, test_clip_720p_b):
        """decide_transitions returns a list for 2 clips."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.decide_transitions(clips)

        assert len(transitions) == 1
        assert transitions[0] in ("fade", "cut")

    def test_decide_transitions_empty(self):
        """decide_transitions with <2 clips returns empty."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        assert engine.decide_transitions([]) == []

    def test_predecided_transitions_override(self, test_clip_720p, test_clip_720p_b):
        """Predecided transitions are used when set in settings."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(predecided_transitions=["cut"])
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["cut"]


# ===================================================================
# assembly_engine.py — assemble_with_cuts, assemble_with_crossfade
# ===================================================================


class TestAssemblyEngineMethods:
    """Tests for alternative assembly methods on AssemblyEngine."""

    def test_assemble_with_cuts(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """assemble_with_cuts produces output with no crossfade overlap."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition=TransitionType.CUT)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "cuts.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_with_cuts(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        # Cuts = no overlap, so ~6s
        assert 5.0 < duration < 7.5

    def test_assemble_with_crossfade(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """assemble_with_crossfade produces output shorter than sum of inputs."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "xfade.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_with_crossfade(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # Two 3s clips with 0.3s crossfade -> ~5.7s
        assert 4.5 < duration < 7.0

    def test_assemble_with_smart_transitions(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """assemble_with_smart_transitions produces valid output."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(transition=TransitionType.SMART)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        output = tmp_path / "smart.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        result = engine.assemble_with_smart_transitions(clips, output)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

    def test_assemble_with_cuts_empty_raises(self):
        """assemble_with_cuts with empty clips raises ValueError."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        with pytest.raises(ValueError, match="No clips"):
            engine.assemble_with_cuts([], Path("/tmp/out.mp4"))

    def test_assemble_with_crossfade_one_clip_raises(self, test_clip_720p):
        """assemble_with_crossfade with 1 clip raises ValueError."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        with pytest.raises(ValueError, match="at least 2"):
            engine.assemble_with_crossfade([_make_clip(test_clip_720p)], Path("/tmp/out.mp4"))

    def test_assemble_with_progress_callback(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Progress callback is invoked during scalable assembly."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        fb = FilterBuilder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder, fb, _noop_cancel)

        progress_calls = []

        def on_progress(pct, msg):
            progress_calls.append((pct, msg))

        output = tmp_path / "progress.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        engine.assemble_scalable(clips, output, progress_callback=on_progress)

        assert len(progress_calls) > 0
        # Should have streaming assembly progress messages
        assert any(
            "Streaming" in msg or "Mixing" in msg or "Muxing" in msg for _, msg in progress_calls
        )


# ===================================================================
# clip_encoder.py — more coverage
# ===================================================================


class TestClipEncoderExtra:
    """Additional ClipEncoder tests for uncovered paths."""

    def test_encode_clip_with_title_screen_flag(self, test_clip_720p, tmp_path):
        """Title screen clips skip loudnorm and use black bar mode."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(normalize_clip_audio=True)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "title.mp4"
        clip = _make_clip(test_clip_720p, is_title_screen=True)
        encoder.encode_single_clip(clip, output, target_resolution=(1280, 720))

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

    def test_log_ffmpeg_error_parses_stderr(self):
        """log_ffmpeg_error extracts error lines from stderr."""
        from immich_memories.processing.clip_encoder import log_ffmpeg_error

        result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="line1\nError: something went wrong\nline3\ninvalid option\n",
        )
        error_msg = log_ffmpeg_error(result)
        assert "Error" in error_msg or "invalid" in error_msg

    def test_log_ffmpeg_error_truncates_long_stderr(self):
        """log_ffmpeg_error truncates very long stderr."""
        from immich_memories.processing.clip_encoder import log_ffmpeg_error

        result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="x" * 5000,
        )
        error_msg = log_ffmpeg_error(result)
        assert len(error_msg) <= 2000

    def test_resolve_encode_resolution_explicit(self):
        """resolve_encode_resolution uses explicit target when provided."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(target_resolution=(1920, 1080))
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        w, h = encoder.resolve_encode_resolution((640, 480))
        assert (w, h) == (640, 480)

    def test_resolve_encode_resolution_from_settings(self):
        """resolve_encode_resolution falls back to settings.target_resolution."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(target_resolution=(1920, 1080))
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        w, h = encoder.resolve_encode_resolution(None)
        assert (w, h) == (1920, 1080)

    def test_resolve_encode_hdr_sdr(self, test_clip_720p):
        """resolve_encode_hdr for SDR clip returns hlg default and empty filter."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(preserve_hdr=False)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        clip = _make_clip(test_clip_720p)
        hdr_type, colorspace = encoder.resolve_encode_hdr(clip)
        assert hdr_type == "hlg"
        assert colorspace == ""

    def test_resolve_encode_hdr_enabled(self, test_clip_720p):
        """resolve_encode_hdr with preserve_hdr=True probes clip and returns filter."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(preserve_hdr=True)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        clip = _make_clip(test_clip_720p)
        hdr_type, colorspace = encoder.resolve_encode_hdr(clip)
        # SDR clip with preserve_hdr still gets hlg default and colorspace filter
        assert hdr_type == "hlg"
        assert "setparams" in colorspace

    def test_trim_segment_reencode(self, test_clip_720p, tmp_path):
        """Re-encode trim produces valid output with audio."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        output = tmp_path / "reencode_trim.mp4"
        encoder.trim_segment_reencode(test_clip_720p, output, start=0.5, duration=1.5)

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        assert 0.5 < duration < 3.0


# ===================================================================
# filter_builder.py — more coverage
# ===================================================================


class TestFilterBuilderExtra:
    """Additional FilterBuilder tests for uncovered paths."""

    def test_build_clip_video_filter_with_rotation(self, test_clip_720p):
        """build_clip_video_filter with rotation_override includes transpose."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        clip = _make_clip(test_clip_720p, rotation_override=90)
        result = fb.build_clip_video_filter(0, clip, ctx)

        assert "transpose=1" in result

    def test_build_clip_video_filter_privacy_mode(self, test_clip_720p):
        """Privacy mode adds gblur to non-title clips."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(privacy_mode=True)
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        clip = _make_clip(test_clip_720p)
        result = fb.build_clip_video_filter(0, clip, ctx)

        assert "gblur" in result

    def test_build_audio_prep_title_screen_uses_silence(self, test_clip_720p):
        """Title screen clips get silent audio (anullsrc)."""
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        clips = [_make_clip(test_clip_720p, is_title_screen=True)]
        parts, labels = fb.build_audio_prep_filters(clips)

        assert len(labels) == 1
        assert "anullsrc" in parts[0]

    def test_get_clip_hdr_conversion_same_type(self, test_clip_720p):
        """get_clip_hdr_conversion returns empty string when types match."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(preserve_hdr=True)
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=["hlg"],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        result = fb.get_clip_hdr_conversion(0, ctx)
        assert result == ""

    def test_get_clip_hdr_conversion_no_hdr(self, test_clip_720p):
        """get_clip_hdr_conversion returns empty when preserve_hdr is False."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings(preserve_hdr=False)
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        ctx = AssemblyContext(
            target_w=1280,
            target_h=720,
            pix_fmt="yuv420p",
            hdr_type="hlg",
            clip_hdr_types=[None],
            clip_primaries=[None],
            colorspace_filter="",
            target_fps=30,
            fade_duration=0.3,
        )
        result = fb.get_clip_hdr_conversion(0, ctx)
        assert result == ""

    def test_build_probed_audio_filters(self, test_clip_720p, test_clip_720p_b):
        """build_probed_audio_filters returns filters with probed durations."""
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        batches = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        audio_durations = [3.0, 3.0]

        parts, labels = fb.build_probed_audio_filters(batches, audio_durations)
        assert len(labels) == 2
        assert len(parts) == 2
        assert "[a0prep]" in labels[0]

    def test_build_probed_xfade_chain(self, test_clip_720p, test_clip_720p_b):
        """build_probed_xfade_chain returns filter parts with offsets from probed durations."""
        from immich_memories.processing.filter_builder import FilterBuilder

        settings = _make_settings()
        prober = _make_prober(settings)
        fb = FilterBuilder(settings, prober, _noop_face_center)

        batches = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        video_durations = [3.0, 3.0]
        audio_labels = ["[a0prep]", "[a1prep]"]

        parts, fv, fa, offset = fb.build_probed_xfade_chain(
            batches, video_durations, 0.3, 30, audio_labels
        )
        assert len(parts) > 0
        assert any("xfade" in p for p in parts)
        assert fv  # final video label
        assert fa  # final audio label


# ===================================================================
# title_inserter.py — more coverage
# ===================================================================


class TestTitleInserterExtra:
    """Additional TitleInserter tests for uncovered paths."""

    def test_build_clips_with_year_dividers(self, test_clip_720p, test_clip_720p_b):
        """build_clips_with_year_dividers inserts year divider clips."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        year_divider_paths = {
            2023: test_clip_720p_b,
            2024: test_clip_720p_b,
        }
        title_settings = TitleScreenSettings(month_divider_duration=2.0)

        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p, date="2024-01-05"),
        ]

        result = ti.build_clips_with_year_dividers(clips, year_divider_paths, title_settings)

        # 2 year dividers + 2 original clips = 4
        assert len(result) == 4
        assert result[0].is_title_screen  # 2023 divider
        assert result[2].is_title_screen  # 2024 divider

    def test_select_divider_strategy_none(self, test_clip_720p):
        """divider_mode='none' returns clips unchanged."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        title_settings = TitleScreenSettings(
            divider_mode="none",
            show_month_dividers=False,
        )

        clips = [_make_clip(test_clip_720p, date="2024-01-15")]

        # WHY: generator not needed because divider_mode="none" skips generation
        result = ti.select_divider_strategy(clips, None, title_settings, None, is_trip=False)
        assert len(result) == 1

    def test_assemble_with_titles_disabled(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Title screens disabled passes through to assemble_fn."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings(title_screens=TitleScreenSettings(enabled=False))
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        output = tmp_path / "no_titles.mp4"
        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]

        called_with = []

        def fake_assemble(clips_list, out, cb):
            called_with.append(len(clips_list))
            import shutil

            shutil.copy2(clips_list[0].path, out)
            return out

        result = ti.assemble_with_titles(clips, output, fake_assemble)
        assert result.exists()
        assert called_with == [2]

    def test_assemble_with_titles_no_settings(self, test_clip_720p, tmp_path):
        """No title_screens setting passes through to assemble_fn."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings(title_screens=None)
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        output = tmp_path / "no_settings.mp4"
        clips = [_make_clip(test_clip_720p)]

        called = []

        def fake_assemble(clips_list, out, cb):
            called.append(True)
            import shutil

            shutil.copy2(clips_list[0].path, out)
            return out

        result = ti.assemble_with_titles(clips, output, fake_assemble)
        assert result.exists()
        assert len(called) == 1

    def test_assemble_with_titles_empty_raises(self):
        """assemble_with_titles with empty clips raises ValueError."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        with pytest.raises(ValueError, match="No clips"):
            ti.assemble_with_titles([], Path("/tmp/out.mp4"), lambda _c, o, _p: o)

    def test_parse_clip_date_bad_format(self, test_clip_720p):
        """Unparseable date returns None."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clip = _make_clip(test_clip_720p, date="not-a-date")
        assert ti.parse_clip_date(clip) is None

    def test_detect_month_changes_no_dates(self, test_clip_720p):
        """Clips without dates produce no month changes."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p)]
        changes = ti.detect_month_changes(clips)
        assert len(changes) == 0

    def test_detect_year_changes_same_year(self, test_clip_720p, test_clip_720p_b):
        """Clips in same year produce single year entry."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p_b, date="2024-06-20"),
        ]
        changes = ti.detect_year_changes(clips)
        assert len(changes) == 1
        assert changes[0] == (0, 2024)

    def test_detect_year_changes_skips_no_date(self, test_clip_720p, test_clip_720p_b):
        """detect_year_changes skips clips without dates."""
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p),  # no date
            _make_clip(test_clip_720p_b, date="2024-06-20"),
        ]
        changes = ti.detect_year_changes(clips)
        # All in 2024, one skipped -> 1 year entry
        assert len(changes) == 1

    def test_generate_year_dividers(self, test_clip_720p, test_clip_720p_b):
        """generate_year_dividers calls generator for each year."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        @dataclass
        class FakeDivider:
            path: Path
            duration: float = 2.0

        class FakeGenerator:
            def generate_year_divider(self, year):
                return FakeDivider(path=test_clip_720p_b)

        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p_b, date="2024-01-05"),
        ]
        title_settings = TitleScreenSettings()

        result = ti.generate_year_dividers(
            clips, FakeGenerator(), title_settings, progress_callback=None
        )
        assert 2023 in result
        assert 2024 in result

    def test_generate_month_dividers(self, test_clip_720p, test_clip_720p_b):
        """generate_month_dividers calls generator for each month change."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        @dataclass
        class FakeDivider:
            path: Path
            duration: float = 2.0

        class FakeGenerator:
            def generate_month_divider(self, month, year, is_birthday_month=False):
                return FakeDivider(path=test_clip_720p_b)

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p_b, date="2024-03-10"),
        ]
        title_settings = TitleScreenSettings(show_month_dividers=True)

        result = ti.generate_month_dividers(
            clips, FakeGenerator(), title_settings, progress_callback=None
        )
        assert (2024, 1) in result
        assert (2024, 3) in result

    def test_generate_month_dividers_disabled(self, test_clip_720p):
        """generate_month_dividers returns empty when dividers disabled."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        clips = [_make_clip(test_clip_720p, date="2024-01-15")]
        title_settings = TitleScreenSettings(show_month_dividers=False)

        result = ti.generate_month_dividers(clips, None, title_settings, None)
        assert result == {}

    def test_select_divider_strategy_year_mode(self, test_clip_720p, test_clip_720p_b):
        """divider_mode='year' calls year divider generation."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        @dataclass
        class FakeDivider:
            path: Path
            duration: float = 2.0

        class FakeGenerator:
            def generate_year_divider(self, year):
                return FakeDivider(path=test_clip_720p_b)

        title_settings = TitleScreenSettings(divider_mode="year")
        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p_b, date="2024-01-05"),
        ]

        result = ti.select_divider_strategy(
            clips, FakeGenerator(), title_settings, None, is_trip=False
        )
        # 2 year dividers + 2 clips = 4
        assert len(result) == 4
        assert result[0].is_title_screen

    def test_select_divider_strategy_month_mode(self, test_clip_720p, test_clip_720p_b):
        """divider_mode='month' calls month divider generation."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_inserter import TitleInserter

        settings = _make_settings()
        prober = _make_prober(settings)
        ti = TitleInserter(settings, prober)

        @dataclass
        class FakeDivider:
            path: Path
            duration: float = 2.0

        class FakeGenerator:
            def generate_month_divider(self, month, year, is_birthday_month=False):
                return FakeDivider(path=test_clip_720p_b)

        title_settings = TitleScreenSettings(
            divider_mode="month",
            show_month_dividers=True,
        )
        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p_b, date="2024-03-10"),
        ]

        result = ti.select_divider_strategy(
            clips, FakeGenerator(), title_settings, None, is_trip=False
        )
        # 2 month dividers + 2 clips = 4
        assert len(result) == 4


# ===================================================================
# hdr_utilities.py — more coverage
# ===================================================================


class TestHDRUtilitiesExtra:
    """Additional HDR utility tests for uncovered paths."""

    def test_check_zscale_available(self):
        """_check_zscale_available returns bool without crashing."""
        from immich_memories.processing.hdr_utilities import _check_zscale_available

        result = _check_zscale_available()
        assert isinstance(result, bool)

    def test_hdr_color_args(self):
        """_hdr_color_args returns correct FFmpeg color args."""
        from immich_memories.processing.hdr_utilities import _hdr_color_args

        args = _hdr_color_args("arib-std-b67")
        assert "-colorspace" in args
        assert "bt2020nc" in args
        assert "arib-std-b67" in args

    def test_encoder_args_macos_sdr(self):
        """_encoder_args_macos SDR mode excludes HDR flags."""
        import sys

        if sys.platform != "darwin":
            pytest.skip("macOS-only test")

        from immich_memories.processing.hdr_utilities import _encoder_args_macos

        args = _encoder_args_macos(crf=23, preserve_hdr=False, color_trc="arib-std-b67")
        args_str = " ".join(args)
        assert "hevc_videotoolbox" in args_str
        assert "p010le" not in args_str

    def test_encoder_args_macos_hdr(self):
        """_encoder_args_macos HDR mode includes p010le and color metadata."""
        import sys

        if sys.platform != "darwin":
            pytest.skip("macOS-only test")

        from immich_memories.processing.hdr_utilities import _encoder_args_macos

        args = _encoder_args_macos(crf=18, preserve_hdr=True, color_trc="arib-std-b67")
        args_str = " ".join(args)
        assert "hevc_videotoolbox" in args_str
        assert "p010le" in args_str
        assert "bt2020" in args_str

    def test_encoder_args_cpu_sdr(self):
        """CPU fallback SDR uses libx264."""
        from immich_memories.processing.hdr_utilities import _encoder_args_cpu

        args = _encoder_args_cpu(
            crf=23, preserve_hdr=False, color_trc="arib-std-b67", hdr_type="hlg"
        )
        assert "-c:v" in args
        assert "libx264" in args

    def test_encoder_args_cpu_hdr(self):
        """CPU fallback HDR uses libx265 with x265-params."""
        from immich_memories.processing.hdr_utilities import _encoder_args_cpu

        args = _encoder_args_cpu(
            crf=18, preserve_hdr=True, color_trc="arib-std-b67", hdr_type="hlg"
        )
        assert "libx265" in args
        assert "-x265-params" in args
