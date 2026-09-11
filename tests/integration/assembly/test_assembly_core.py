"""Integration tests for the assembly core: engine, encoder, title_inserter, hdr.

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
    from immich_memories.processing.assembly_config import (
        AssemblySettings,
        TransitionType,
        standalone_assembly_encoding_plan,
    )

    defaults = {
        "encoding_plan": standalone_assembly_encoding_plan(28),
        "transition": TransitionType.CROSSFADE,
        "transition_duration": 0.3,
        "auto_resolution": False,
        "target_resolution": (1280, 720),
        "normalize_clip_audio": False,
    }
    defaults.update(overrides)
    return AssemblySettings(**defaults)


def _hlg_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.H265,
        encoder="libx265",
        encoder_args=("-preset", "ultrafast", "-crf", "28"),
        target_transfer=HdrTransfer.HLG,
        tone_map_to_sdr=False,
        pixel_format="yuv420p10le",
        container="mp4",
    )


def _make_clip(path: Path, duration: float = 3.0, **kwargs):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration, **kwargs)


def _make_prober(settings=None):
    from immich_memories.processing.ffmpeg_prober import FFmpegProber

    return FFmpegProber(settings or _make_settings())


def _noop_face_center(_path: Path) -> tuple[float, float] | None:
    return None


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

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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

        settings = _make_settings(target_resolution=(1280, 720))
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

        with pytest.raises(ValueError, match="No clips"):
            engine.assemble_scalable([], Path("/tmp/out.mp4"))

    def test_assemble_with_cut_transitions(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Pre-decided cut transitions produce output with full combined duration."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(
            transition=TransitionType.CUT,
            predecided_transitions=["cut"],
        )
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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


# ===================================================================
# title_inserter.py
# ===================================================================


class TestTitleInserter:
    """Tests for TitleInserter date parsing, month detection, and divider building."""

    def test_parse_clip_date_iso(self, test_clip_720p):
        """parse_clip_date handles ISO date format."""
        from immich_memories.processing.title_divider_planner import parse_clip_date

        clip = _make_clip(test_clip_720p, date="2024-06-15")
        result = parse_clip_date(clip)
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15

    def test_parse_clip_date_datetime(self, test_clip_720p):
        """parse_clip_date handles datetime format."""
        from immich_memories.processing.title_divider_planner import parse_clip_date

        clip = _make_clip(test_clip_720p, date="2024-06-15T14:30:00")
        result = parse_clip_date(clip)
        assert result is not None
        assert result.year == 2024
        assert result.month == 6

    def test_parse_clip_date_none(self, test_clip_720p):
        """parse_clip_date returns None for missing date."""
        from immich_memories.processing.title_divider_planner import parse_clip_date

        clip = _make_clip(test_clip_720p, date=None)
        assert parse_clip_date(clip) is None

    def test_detect_month_changes(self, test_clip_720p, test_clip_720p_b):
        """detect_month_changes finds transitions between different months."""
        from immich_memories.processing.title_divider_planner import detect_month_changes

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p, date="2024-01-20"),
            _make_clip(test_clip_720p_b, date="2024-03-10"),
        ]
        changes = detect_month_changes(clips)

        # First clip starts Jan, then March = 2 month groups
        assert len(changes) == 2
        assert changes[0] == (0, 1, 2024)  # Jan starts at index 0
        assert changes[1] == (2, 3, 2024)  # Mar starts at index 2

    def test_detect_year_changes(self, test_clip_720p, test_clip_720p_b):
        """detect_year_changes finds year transitions."""
        from immich_memories.processing.title_divider_planner import detect_year_changes

        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p_b, date="2024-01-05"),
        ]
        changes = detect_year_changes(clips)

        assert len(changes) == 2
        assert changes[0] == (0, 2023)
        assert changes[1] == (1, 2024)

    def test_build_clips_with_dividers(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """build_clips_with_dividers inserts month divider clips at boundaries."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

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

        # WHY: no generator — the divider videos already exist in month_divider_paths
        planner = TitleDividerPlanner(None, title_settings)
        result = planner.build_clips_with_dividers(clips, month_divider_paths)

        # WHY: first month divider is skipped (intro title covers it).
        # 1 divider (Mar only) + 3 original clips = 4
        assert len(result) == 4
        assert not result[0].is_title_screen  # Jan clip (no divider)
        assert not result[1].is_title_screen  # Jan clip
        assert result[2].is_title_screen  # Mar divider
        assert not result[3].is_title_screen  # Mar clip


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

    def test_get_hdr_conversion_filter_same_type(self):
        """Same source and target HDR type returns empty string."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        result = _get_hdr_conversion_filter("hlg", "hlg")
        assert result == ""

    def test_get_hdr_conversion_filter_sdr_to_hlg(self):
        """Required SDR-to-HLG conversion either runs or fails closed."""
        from immich_memories.processing.hdr_utilities import (
            RequiredColorConversionUnavailable,
            _get_hdr_conversion_filter,
            check_zscale_available,
        )

        if not check_zscale_available():
            with pytest.raises(RequiredColorConversionUnavailable):
                _get_hdr_conversion_filter(None, "hlg", required=True)
            return

        result = _get_hdr_conversion_filter(None, "hlg", required=True)
        assert "zscale=" in result
        assert "t=arib-std-b67" in result


# ===================================================================
# assembly_engine.py — context building
# ===================================================================


class TestAssemblyContext:
    """Tests for create_assembly_context and resolve_target_resolution."""

    def test_create_assembly_context_sdr(self, test_clip_720p):
        """SDR context has yuv420p pixel format and empty colorspace filter."""
        from immich_memories.processing.assembly_engine import create_assembly_context

        settings = _make_settings()
        prober = _make_prober(settings)
        clips = [_make_clip(test_clip_720p)]

        ctx = create_assembly_context(settings, prober, clips, 1280, 720)

        assert ctx.target_w == 1280
        assert ctx.target_h == 720
        assert ctx.pix_fmt == "yuv420p"
        assert ctx.hdr_type == "sdr"
        assert "colorspace=bt709" in ctx.colorspace_filter

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
    """Tests for get_transition_types."""

    def test_get_transition_types_crossfade(self, test_clip_720p, test_clip_720p_b):
        """CROSSFADE setting produces all fade transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(transition=TransitionType.CROSSFADE)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["fade"]

    def test_get_transition_types_cut(self, test_clip_720p, test_clip_720p_b):
        """CUT setting produces all cut transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(transition=TransitionType.CUT)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["cut"]

    def test_title_screen_forces_fade(self, test_clip_720p, test_clip_720p_b):
        """Title screen clips always get fade transitions."""
        from immich_memories.processing.assembly_config import TransitionType
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(transition=TransitionType.CUT)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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

        settings = _make_settings(transition_duration=0.5)
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

        transitions = ["fade"]
        # Clip durations too short for 0.5s fade (min = 1.0s needed)
        clip_durations = [0.5, 0.5]

        result = engine._validate_fade_transitions(transitions, clip_durations, 0.5)
        assert result == ["cut"]

    def test_predecided_transitions_override(self, test_clip_720p, test_clip_720p_b):
        """Predecided transitions are used when set in settings."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(predecided_transitions=["cut"])
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p_b)]
        transitions = engine.get_transition_types(clips)

        assert transitions == ["cut"]


# ===================================================================
# assembly_engine.py — progress reporting
# ===================================================================


class TestAssemblyEngineMethods:
    """Tests for assembly progress reporting on AssemblyEngine."""

    def test_assemble_with_progress_callback(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Progress callback is invoked during scalable assembly."""
        from immich_memories.processing.assembly_engine import AssemblyEngine
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)
        engine = AssemblyEngine(settings, prober, encoder)

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

        settings = _make_settings()
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        clip = _make_clip(test_clip_720p)
        hdr_type, colorspace = encoder.resolve_encode_hdr(clip)
        assert hdr_type == "sdr"
        assert "colorspace=bt709" in colorspace

    def test_resolve_encode_hdr_enabled(self, test_clip_720p):
        """An explicit HLG plan probes and converts an SDR clip."""
        from immich_memories.processing.clip_encoder import ClipEncoder

        settings = _make_settings(encoding_plan=_hlg_plan())
        prober = _make_prober(settings)
        encoder = ClipEncoder(settings, prober, _noop_face_center)

        clip = _make_clip(test_clip_720p)
        hdr_type, colorspace = encoder.resolve_encode_hdr(clip)
        assert hdr_type == "hlg"
        assert "zscale" in colorspace
        assert "setparams" in colorspace


# ===================================================================
# hdr per-clip resolution — more coverage
# ===================================================================


class TestClipHdrResolutionExtra:
    """Per-clip HDR resolution from an AssemblyContext."""

    def test_matching_transfer_needs_no_conversion(self, test_clip_720p):
        """A clip already in the target transfer gets an empty conversion filter."""
        from immich_memories.processing.ffmpeg_runner import AssemblyContext
        from immich_memories.processing.hdr_utilities import _resolve_clip_hdr

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
        conversion, *_ = _resolve_clip_hdr(0, ctx, ctx.hdr_type)
        assert conversion == ""


# ===================================================================
# title_inserter.py — more coverage
# ===================================================================


class TestTitleInserterExtra:
    """Additional TitleInserter tests for uncovered paths."""

    def test_build_clips_with_year_dividers(self, test_clip_720p, test_clip_720p_b):
        """build_clips_with_year_dividers inserts year divider clips."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        year_divider_paths = {
            2023: test_clip_720p_b,
            2024: test_clip_720p_b,
        }
        title_settings = TitleScreenSettings(month_divider_duration=2.0)

        clips = [
            _make_clip(test_clip_720p, date="2023-12-25"),
            _make_clip(test_clip_720p, date="2024-01-05"),
        ]

        # WHY: no generator — the divider videos already exist in year_divider_paths
        planner = TitleDividerPlanner(None, title_settings)
        result = planner.build_clips_with_year_dividers(clips, year_divider_paths)

        # 2 year dividers + 2 original clips = 4
        assert len(result) == 4
        assert result[0].is_title_screen  # 2023 divider
        assert result[2].is_title_screen  # 2024 divider

    def test_select_divider_strategy_none(self, test_clip_720p):
        """divider_mode='none' returns clips unchanged."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        title_settings = TitleScreenSettings(
            divider_mode="none",
            show_month_dividers=False,
        )

        clips = [_make_clip(test_clip_720p, date="2024-01-15")]

        # WHY: generator not needed because divider_mode="none" skips generation
        planner = TitleDividerPlanner(None, title_settings)
        result = planner.select_divider_strategy(clips, None, is_trip=False)
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
        from immich_memories.processing.title_divider_planner import parse_clip_date

        clip = _make_clip(test_clip_720p, date="not-a-date")
        assert parse_clip_date(clip) is None

    def test_detect_month_changes_no_dates(self, test_clip_720p):
        """Clips without dates produce no month changes."""
        from immich_memories.processing.title_divider_planner import detect_month_changes

        clips = [_make_clip(test_clip_720p), _make_clip(test_clip_720p)]
        changes = detect_month_changes(clips)
        assert len(changes) == 0

    def test_detect_year_changes_same_year(self, test_clip_720p, test_clip_720p_b):
        """Clips in same year produce single year entry."""
        from immich_memories.processing.title_divider_planner import detect_year_changes

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p_b, date="2024-06-20"),
        ]
        changes = detect_year_changes(clips)
        assert len(changes) == 1
        assert changes[0] == (0, 2024)

    def test_detect_year_changes_skips_no_date(self, test_clip_720p, test_clip_720p_b):
        """detect_year_changes skips clips without dates."""
        from immich_memories.processing.title_divider_planner import detect_year_changes

        clips = [
            _make_clip(test_clip_720p, date="2024-01-15"),
            _make_clip(test_clip_720p),  # no date
            _make_clip(test_clip_720p_b, date="2024-06-20"),
        ]
        changes = detect_year_changes(clips)
        # All in 2024, one skipped -> 1 year entry
        assert len(changes) == 1

    def test_generate_year_dividers(self, test_clip_720p, test_clip_720p_b):
        """generate_year_dividers calls generator for each year."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

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

        planner = TitleDividerPlanner(FakeGenerator(), title_settings)
        result = planner.generate_year_dividers(clips, progress_callback=None)
        assert 2023 in result
        assert 2024 in result

    def test_generate_month_dividers(self, test_clip_720p, test_clip_720p_b):
        """generate_month_dividers calls generator for each month change."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

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

        planner = TitleDividerPlanner(FakeGenerator(), title_settings)
        result = planner.generate_month_dividers(clips, progress_callback=None)
        assert (2024, 1) in result
        assert (2024, 3) in result

    def test_planned_month_dividers_ignore_clip_threshold(
        self, test_clip_720p, test_clip_720p_b
    ) -> None:
        """A finalized plan renders every selected month after the opening month."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        @dataclass
        class FakeDivider:
            path: Path

        class FakeGenerator:
            def generate_month_divider(self, month, year, is_birthday_month=False):
                return FakeDivider(path=test_clip_720p_b)

        clips = [
            _make_clip(test_clip_720p, date="2026-05-05"),
            _make_clip(test_clip_720p_b, date="2026-06-05"),
            _make_clip(test_clip_720p, date="2026-07-05"),
        ]
        title_settings = TitleScreenSettings(
            show_month_dividers=True,
            month_divider_threshold=99,
            max_dividers=2,
        )

        planner = TitleDividerPlanner(FakeGenerator(), title_settings)
        paths = planner.generate_month_dividers(clips, progress_callback=None)

        assert set(paths) == {(2026, 6), (2026, 7)}

    def test_generate_month_dividers_disabled(self, test_clip_720p):
        """generate_month_dividers returns empty when dividers disabled."""
        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

        clips = [_make_clip(test_clip_720p, date="2024-01-15")]
        title_settings = TitleScreenSettings(show_month_dividers=False)

        # WHY: no generator — disabled dividers never reach one
        result = TitleDividerPlanner(None, title_settings).generate_month_dividers(clips, None)
        assert result == {}

    def test_select_divider_strategy_year_mode(self, test_clip_720p, test_clip_720p_b):
        """divider_mode='year' calls year divider generation."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

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

        planner = TitleDividerPlanner(FakeGenerator(), title_settings)
        result = planner.select_divider_strategy(clips, None, is_trip=False)
        # 2 year dividers + 2 clips = 4
        assert len(result) == 4
        assert result[0].is_title_screen

    def test_select_divider_strategy_month_mode(self, test_clip_720p, test_clip_720p_b):
        """divider_mode='month' calls month divider generation."""
        from dataclasses import dataclass

        from immich_memories.processing.assembly_config import TitleScreenSettings
        from immich_memories.processing.title_divider_planner import TitleDividerPlanner

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

        planner = TitleDividerPlanner(FakeGenerator(), title_settings)
        result = planner.select_divider_strategy(clips, None, is_trip=False)
        # 1 month divider (first skipped) + 2 clips = 3
        assert len(result) == 3


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
