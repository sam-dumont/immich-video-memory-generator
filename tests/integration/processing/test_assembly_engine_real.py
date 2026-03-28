"""Integration tests for AssemblyEngine with real FFmpeg.

Run with: make test-integration-processing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _make_settings(**kwargs):
    from immich_memories.processing.assembly_config import AssemblySettings

    defaults = {
        "preserve_hdr": False,
        "auto_resolution": False,
        "target_resolution": (1280, 720),
        "transition_duration": 0.5,
    }
    defaults.update(kwargs)
    return AssemblySettings(**defaults)


def _make_engine(settings=None):
    from immich_memories.processing.assembly_engine import AssemblyEngine
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.filter_builder import FilterBuilder

    def _no_face(_path: Path) -> tuple[float, float] | None:
        return None

    if settings is None:
        settings = _make_settings()
    prober = FFmpegProber(settings)
    encoder = ClipEncoder(settings=settings, prober=prober, face_center_fn=_no_face)
    filter_builder = FilterBuilder(settings=settings, prober=prober, face_center_fn=_no_face)
    return AssemblyEngine(
        settings=settings,
        prober=prober,
        encoder=encoder,
        filter_builder=filter_builder,
        check_cancelled_fn=lambda: None,
    )


def _make_clip(path: Path, duration: float = 3.0):
    from immich_memories.processing.assembly_config import AssemblyClip

    return AssemblyClip(path=path, duration=duration)


# ---------------------------------------------------------------------------
# Single clip assembly
# ---------------------------------------------------------------------------


class TestSingleClipAssembly:
    def test_single_clip_copies_to_output(self, test_clip_720p: Path, tmp_path: Path):
        """Single clip assembly copies the file (no encoding needed)."""
        engine = _make_engine()
        clip = _make_clip(test_clip_720p, duration=3.0)
        out = tmp_path / "single.mp4"

        result = engine.assemble_scalable([clip], out)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")


# ---------------------------------------------------------------------------
# Two clip crossfade assembly
# ---------------------------------------------------------------------------


class TestTwoClipAssembly:
    def test_two_clips_crossfade(
        self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path
    ):
        """Two clips assembled with streaming crossfade produce valid output."""
        engine = _make_engine()
        clips = [
            _make_clip(test_clip_720p, duration=3.0),
            _make_clip(test_clip_720p_b, duration=3.0),
        ]
        out = tmp_path / "two_clip.mp4"

        result = engine.assemble_scalable(clips, out)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        dur = get_duration(probe)
        # 3 + 3 - 0.5 fade = 5.5s expected, allow tolerance
        assert dur > 4.0, f"Expected >4s for two 3s clips with crossfade, got {dur}"


# ---------------------------------------------------------------------------
# Three clip assembly
# ---------------------------------------------------------------------------


class TestThreeClipAssembly:
    def test_three_clips(self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path):
        """Three clips assembled produce valid multi-clip output."""
        engine = _make_engine()
        clips = [
            _make_clip(test_clip_720p, duration=3.0),
            _make_clip(test_clip_720p_b, duration=3.0),
            _make_clip(test_clip_720p, duration=3.0),
        ]
        out = tmp_path / "three_clip.mp4"

        result = engine.assemble_scalable(clips, out)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        dur = get_duration(probe)
        # 3 + 3 + 3 - 2*0.5 = 8.0s expected, allow tolerance
        assert dur > 6.0, f"Expected >6s for three 3s clips, got {dur}"


# ---------------------------------------------------------------------------
# Mixed resolution assembly
# ---------------------------------------------------------------------------


class TestMixedResolutionAssembly:
    def test_landscape_and_portrait(
        self, test_clip_720p: Path, portrait_clip: Path, tmp_path: Path
    ):
        """Assembling landscape + portrait clips doesn't crash."""
        engine = _make_engine()
        clips = [
            _make_clip(test_clip_720p, duration=3.0),
            _make_clip(portrait_clip, duration=2.0),
        ]
        out = tmp_path / "mixed_res.mp4"

        result = engine.assemble_scalable(clips, out)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


class TestProgressCallback:
    def test_callback_invoked(self, test_clip_720p: Path, test_clip_720p_b: Path, tmp_path: Path):
        """Progress callback is called at least once during assembly."""
        engine = _make_engine()
        clips = [
            _make_clip(test_clip_720p, duration=3.0),
            _make_clip(test_clip_720p_b, duration=3.0),
        ]
        out = tmp_path / "progress.mp4"
        calls: list[tuple[float, str]] = []

        def on_progress(pct: float, msg: str):
            calls.append((pct, msg))

        engine.assemble_scalable(clips, out, progress_callback=on_progress)

        assert len(calls) > 0, "Progress callback should be called at least once"


# ---------------------------------------------------------------------------
# Short clip transition handling
# ---------------------------------------------------------------------------


class TestShortClipTransitions:
    def test_short_clips_downgrade_to_cut(self, short_clip: Path, tmp_path: Path):
        """Clips shorter than 2x fade duration get downgraded from fade to cut."""
        engine = _make_engine(_make_settings(transition_duration=0.5))
        # short_clip is 1s — less than 2*0.5=1.0, so fade should be downgraded
        clips = [
            _make_clip(short_clip, duration=1.0),
            _make_clip(short_clip, duration=1.0),
        ]
        out = tmp_path / "short_transition.mp4"

        result = engine.assemble_scalable(clips, out)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")


# ---------------------------------------------------------------------------
# resolve_target_resolution
# ---------------------------------------------------------------------------


class TestResolveTargetResolution:
    def test_returns_valid_dimensions(self, test_clip_720p: Path):
        from immich_memories.processing.assembly_engine import resolve_target_resolution
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = _make_settings()
        prober = FFmpegProber(settings)
        clips = [_make_clip(test_clip_720p)]
        w, h = resolve_target_resolution(settings, prober, clips)
        assert w > 0 and h > 0
        assert w == 1280 and h == 720  # We set target_resolution=(1280,720)

    def test_auto_resolution_detects_from_clips(self, test_clip_720p: Path):
        from immich_memories.processing.assembly_engine import resolve_target_resolution
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = _make_settings(auto_resolution=True, target_resolution=None)
        prober = FFmpegProber(settings)
        clips = [_make_clip(test_clip_720p)]
        w, h = resolve_target_resolution(settings, prober, clips)
        assert w > 0 and h > 0


# ---------------------------------------------------------------------------
# create_assembly_context
# ---------------------------------------------------------------------------


class TestCreateAssemblyContext:
    def test_returns_valid_context(self, test_clip_720p: Path):
        from immich_memories.processing.assembly_engine import create_assembly_context
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = _make_settings()
        prober = FFmpegProber(settings)
        clips = [_make_clip(test_clip_720p)]
        ctx = create_assembly_context(settings, prober, clips, target_w=1280, target_h=720)

        assert ctx.target_w == 1280
        assert ctx.target_h == 720
        assert ctx.target_fps > 0
        assert ctx.pix_fmt == "yuv420p"  # preserve_hdr=False
        assert ctx.fade_duration > 0

    def test_context_auto_resolves_resolution(self, test_clip_720p: Path):
        from immich_memories.processing.assembly_engine import create_assembly_context
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        settings = _make_settings()
        prober = FFmpegProber(settings)
        clips = [_make_clip(test_clip_720p)]
        # Pass None for target_w/h to trigger auto-resolve
        ctx = create_assembly_context(settings, prober, clips)

        assert ctx.target_w > 0
        assert ctx.target_h > 0


# ---------------------------------------------------------------------------
# decide_transitions
# ---------------------------------------------------------------------------


class TestDecideTransitions:
    def test_two_clips_produce_one_transition(self, test_clip_720p: Path):
        engine = _make_engine()
        clips = [
            _make_clip(test_clip_720p, duration=3.0),
            _make_clip(test_clip_720p, duration=3.0),
        ]
        transitions = engine.decide_transitions(clips)
        assert len(transitions) == 1
        assert transitions[0] in ("fade", "cut")

    def test_single_clip_empty_transitions(self, test_clip_720p: Path):
        engine = _make_engine()
        transitions = engine.decide_transitions([_make_clip(test_clip_720p)])
        assert transitions == []
