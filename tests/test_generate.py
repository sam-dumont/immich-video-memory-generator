"""Tests for the generate_memory() orchestrator and helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import (
    GenerationError,
    GenerationParams,
    _build_assembly_settings,
    _report,
    _total_clip_duration,
    assets_to_clips,
)
from immich_memories.generate_music import music_config_available
from immich_memories.generate_privacy import clip_location_name
from tests.conftest import make_asset, make_clip


class TestGenerationParams:
    def test_defaults(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        assert params.transition == "crossfade"
        assert params.upload_enabled is False
        assert params.music_path is None
        assert params.privacy_mode is False

    def test_progress_callback_called(self):
        calls = []
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            progress_callback=lambda phase, pct, msg: calls.append((phase, pct, msg)),
        )
        _report(params, "test", 0.5, "halfway")
        assert len(calls) == 1
        assert calls[0] == ("test", 0.5, "halfway")

    def test_no_callback_is_noop(self):
        params = GenerationParams(clips=[], output_path=Path("/tmp/out.mp4"), config=Config())
        _report(params, "test", 0.5, "halfway")  # Should not raise


class TestGenerationError:
    def test_is_exception(self):
        with pytest.raises(GenerationError, match="test error"):
            raise GenerationError("test error")


class TestBuildAssemblySettings:
    def test_crossfade_transition(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            transition="crossfade",
            transition_duration=0.3,
        )
        settings = _build_assembly_settings(params, [])
        from immich_memories.processing.assembly_config import TransitionType

        assert settings.transition == TransitionType.CROSSFADE
        assert settings.transition_duration == 0.3

    def test_cut_transition(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            transition="cut",
        )
        settings = _build_assembly_settings(params, [])
        from immich_memories.processing.assembly_config import TransitionType

        assert settings.transition == TransitionType.CUT

    def test_explicit_resolution(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            output_resolution="1080p",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.target_resolution == (1920, 1080)
        assert settings.auto_resolution is False

    def test_no_resolution_uses_config_default(self):
        """When output_resolution is None (user didn't specify), use config default."""
        config = Config()
        config.output.resolution = "1080p"
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=config,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.auto_resolution is False
        assert settings.target_resolution == (1920, 1080)

    def test_explicit_auto_enables_auto_detection(self):
        """When output_resolution is 'auto', enable source-based auto-detection."""
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            output_resolution="auto",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.auto_resolution is True
        assert settings.target_resolution is None

    def test_no_resolution_uses_config_720p(self):
        """Config default of 720p is respected when no CLI flag given."""
        config = Config()
        config.output.resolution = "720p"
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=config,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.auto_resolution is False
        assert settings.target_resolution == (1280, 720)

    def test_privacy_mode_passed_through(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            privacy_mode=True,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.privacy_mode is True


class TestAssetsToClips:
    def test_filters_short_clips(self):
        assets = [
            make_asset(duration="0:00:00.500"),  # Too short
            make_asset("a2", duration="0:00:05.000"),  # OK
            make_asset("a3", duration="0:00:10.000"),  # OK
        ]
        clips = assets_to_clips(assets)
        assert len(clips) == 2

    def test_empty_assets(self):
        assert assets_to_clips([]) == []

    def test_preserves_duration(self):
        assets = [make_asset(duration="0:00:07.500")]
        clips = assets_to_clips(assets)
        assert clips[0].duration_seconds == 7.5


class TestTotalClipDuration:
    def test_sums_durations(self):
        clips = [make_clip(duration=3.0), make_clip("c2", duration=5.0)]
        params = GenerationParams(
            clips=clips,
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        assert _total_clip_duration(params) == 8


class TestTripLocations:
    def test_extract_trip_locations_deduplicates(self):
        """Clips with GPS data produce unique location list."""
        from immich_memories.generate_privacy import extract_trip_locations
        from immich_memories.processing.assembly_config import AssemblyClip

        clips = [
            AssemblyClip(
                path=Path("/fake/a.mp4"), duration=3.0, latitude=48.8566, longitude=2.3522
            ),
            AssemblyClip(
                path=Path("/fake/b.mp4"), duration=3.0, latitude=48.8566, longitude=2.3522
            ),
            AssemblyClip(
                path=Path("/fake/c.mp4"), duration=3.0, latitude=51.5074, longitude=-0.1278
            ),
        ]
        locations = extract_trip_locations(clips)
        assert len(locations) == 2

    def testgenerate_trip_title_text(self):
        from immich_memories.generate_privacy import generate_trip_title_text

        result = generate_trip_title_text(
            {
                "location_name": "Barcelona",
                "trip_start": date(2025, 7, 1),
                "trip_end": date(2025, 7, 7),
            }
        )
        assert result is not None
        assert "barcelona" in result.lower()

    def test_generate_trip_title_returns_none_without_params(self):
        from immich_memories.generate_privacy import generate_trip_title_text

        assert generate_trip_title_text({}) is None


class TestTitleOverride:
    def test_custom_title_passed_to_settings(self):
        from immich_memories.generate import _build_title_settings

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            title="My Custom Title",
            subtitle="Summer 2025",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        title_settings = _build_title_settings(params, Config(), [])
        assert title_settings.title_override == "My Custom Title"
        assert title_settings.subtitle_override == "Summer 2025"


class TestMusicConfigAvailable:
    def test_returns_false_when_nothing_configured(self):
        config = Config()
        assert music_config_available(config) is False

    def test_returns_true_when_musicgen_enabled(self):
        config = Config()
        config.musicgen.enabled = True
        assert music_config_available(config) is True

    def test_returns_true_when_ace_step_enabled(self):
        config = Config()
        config.ace_step.enabled = True
        assert music_config_available(config) is True

    def test_returns_false_when_both_disabled(self):
        config = Config()
        config.musicgen.enabled = False
        config.ace_step.enabled = False
        assert music_config_available(config) is False


class TestAutoMusicGeneration:
    """Test that generate_memory() auto-generates music when config is available."""

    def test_no_music_flag_skips_generation(self):
        """no_music=True should prevent any music generation."""
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            no_music=True,
        )
        assert params.no_music is True

    def test_no_music_defaults_false(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        assert params.no_music is False

    def test_auto_music_called_when_config_available(self, tmp_path):
        """When no music_path and config has music backends, auto-generate is called."""
        from immich_memories.generate_music import auto_generate_music
        from immich_memories.processing.assembly_config import AssemblyClip

        config = Config()
        config.musicgen.enabled = True

        assembly_clips = [
            AssemblyClip(path=tmp_path / "a.mp4", duration=5.0),
        ]
        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "out.mp4",
            config=config,
        )

        # WHY: mock the async music generation to avoid real API calls
        with patch(
            "immich_memories.generate_music.asyncio.run",
        ) as mock_run:
            from immich_memories.audio.music_generator_models import (
                GeneratedMusic,
                MusicGenerationResult,
                VideoTimeline,
            )

            fake_music_path = tmp_path / "music.wav"
            fake_music_path.write_bytes(b"fake audio")
            mock_run.return_value = MusicGenerationResult(
                versions=[
                    GeneratedMusic(
                        version_id=0,
                        full_mix=fake_music_path,
                        duration=30.0,
                        prompt="test",
                        mood="happy",
                    )
                ],
                timeline=VideoTimeline(),
                mood="happy",
            )

            result = auto_generate_music(
                params.config, assembly_clips, tmp_path / "run_output", params.memory_type
            )
            assert result is not None
            assert result == fake_music_path

    def test_auto_music_returns_none_when_no_config(self, tmp_path):
        """When no music backend is configured, auto-generate returns None."""
        from immich_memories.generate_music import auto_generate_music
        from immich_memories.processing.assembly_config import AssemblyClip

        config = Config()
        # Both disabled by default
        assembly_clips = [
            AssemblyClip(path=tmp_path / "a.mp4", duration=5.0),
        ]
        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "out.mp4",
            config=config,
        )
        result = auto_generate_music(
            params.config, assembly_clips, tmp_path / "run_output", params.memory_type
        )
        assert result is None

    def test_auto_music_returns_none_on_failure(self, tmp_path):
        """If music generation fails, return None instead of crashing."""
        from immich_memories.generate_music import auto_generate_music
        from immich_memories.processing.assembly_config import AssemblyClip

        config = Config()
        config.musicgen.enabled = True

        assembly_clips = [
            AssemblyClip(path=tmp_path / "a.mp4", duration=5.0),
        ]
        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "out.mp4",
            config=config,
        )

        # WHY: mock to simulate API failure
        with patch(
            "immich_memories.generate_music.asyncio.run",
            side_effect=RuntimeError("API unreachable"),
        ):
            result = auto_generate_music(
                params.config, assembly_clips, tmp_path / "run_output", params.memory_type
            )
            assert result is None


class TestClipLocationName:
    def test_returns_city_and_country(self):
        exif = type("Exif", (), {"city": "Paris", "country": "France"})()
        assert clip_location_name(exif) == "Paris, France"

    def test_returns_country_if_no_city(self):
        exif = type("Exif", (), {"city": None, "country": "US"})()
        assert clip_location_name(exif) == "US"

    def test_returns_none_if_no_location(self):
        exif = type("Exif", (), {"city": None, "country": None})()
        assert clip_location_name(exif) is None

    def test_returns_none_for_none_exif(self):
        assert clip_location_name(None) is None


class TestPhaseAllocation:
    """_PipelineProgress scales phase progress into the overall range."""

    def test_pipeline_progress_scales_assembly(self):
        """Assembly callback should map 0.0-1.0 into the assembly phase range."""
        from immich_memories.generate import _PipelineProgress

        calls: list[tuple[str, float, str]] = []

        def on_progress(phase: str, pct: float, msg: str) -> None:
            calls.append((phase, pct, msg))

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            progress_callback=on_progress,
        )
        pp = _PipelineProgress(params, clip_count=10)
        cb = pp.assembly_callback()
        assert cb is not None

        cb(0.0, "Starting")
        cb(0.5, "Halfway")
        cb(1.0, "Done")

        assert all(phase == "assembly" for phase, _, _ in calls)
        assert calls[0][1] > 0.0
        assert calls[-1][1] < 1.0
        pcts = [pct for _, pct, _ in calls]
        assert pcts == sorted(pcts)

    def test_pipeline_progress_phases_are_monotonic(self):
        """Reporting across phases produces monotonically increasing values."""
        from immich_memories.generate import _PipelineProgress

        calls: list[float] = []

        def on_progress(phase: str, pct: float, msg: str) -> None:
            calls.append(pct)

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
            progress_callback=on_progress,
        )
        pp = _PipelineProgress(params, clip_count=10)

        pp.report("download", 0.0, "start")
        pp.report("download", 1.0, "done")
        pp.report("photos", 0.0, "start")
        pp.report("photos", 1.0, "done")
        pp.report("assembly", 0.0, "start")
        pp.report("assembly", 0.5, "mid")
        pp.report("assembly", 1.0, "done")
        pp.report("music", 0.0, "start")
        pp.report("music", 1.0, "done")

        assert calls == sorted(calls), f"Progress not monotonic: {calls}"
        assert calls[-1] == 1.0

    def test_assembly_callback_none_without_progress(self):
        """If no progress_callback on params, assembly callback should be None."""
        from immich_memories.generate import _PipelineProgress

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/out.mp4"),
            config=Config(),
        )
        pp = _PipelineProgress(params, clip_count=5)
        assert pp.assembly_callback() is None


class TestQuietModeProgressCallback:
    """Quiet mode should produce structured log lines instead of Rich progress."""

    def test_quiet_progress_emits_structured_logs(self):
        """Quiet-mode callback emits structured key=value log lines."""
        from immich_memories.cli._progress import make_quiet_progress_callback

        log_lines: list[str] = []
        cb = make_quiet_progress_callback(log_fn=log_lines.append)

        cb("extract", 0.3, "Downloading clip_001.mp4")
        cb("assemble", 0.7, "Encoding (1:30 / 3:00) — 50%")

        assert len(log_lines) == 2
        assert "phase=extract" in log_lines[0]
        assert "pct=30" in log_lines[0]
        assert "phase=assemble" in log_lines[1]
        assert "pct=70" in log_lines[1]

    def test_quiet_progress_throttles_updates(self):
        """Quiet callback throttles to avoid spamming logs."""
        from immich_memories.cli._progress import make_quiet_progress_callback

        log_lines: list[str] = []
        cb = make_quiet_progress_callback(log_fn=log_lines.append, min_interval=10.0)

        # Rapid fire — only first should go through due to throttle
        for i in range(100):
            cb("assemble", i / 100, f"Frame {i}")

        # Should have at most a few lines, not 100
        assert len(log_lines) < 10


class TestApplyMusicFileAtomic:
    """_apply_music_file must use atomic replace for crash-safe swap."""

    def test_replaces_video_with_mixed_output(self, tmp_path):
        """After mixing, video_path has mixed content and no temp file remains."""
        from immich_memories.generate_music import apply_music_file

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")

        # WHY: mock the audio mixer to avoid real FFmpeg — we only test the file swap
        with patch(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
        ) as mock_mix:

            def fake_mix(video_path, music_path, output_path, config):
                output_path.write_bytes(b"mixed video")

            mock_mix.side_effect = fake_mix

            apply_music_file(video, music, volume=0.8)

        assert video.read_bytes() == b"mixed video"
        assert not (tmp_path / "output.with_music.mp4").exists()

    def test_does_not_unlink_original_before_swap(self, tmp_path):
        """Crash-safety: must not unlink() then rename() — use replace() instead."""
        from immich_memories.generate_music import apply_music_file

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")

        unlink_calls: list[Path] = []
        original_unlink = Path.unlink

        def tracking_unlink(self, missing_ok=False):
            unlink_calls.append(self)
            return original_unlink(self, missing_ok=missing_ok)

        # WHY: mock the audio mixer to avoid real FFmpeg — we only test the swap
        with (
            patch("immich_memories.audio.mixer.mix_audio_with_ducking") as mock_mix,
            patch.object(Path, "unlink", tracking_unlink),
        ):

            def fake_mix(video_path, music_path, output_path, config):
                output_path.write_bytes(b"mixed video")

            mock_mix.side_effect = fake_mix

            apply_music_file(video, music, volume=0.8)

        # The original video path must NOT appear in unlink calls
        assert video not in unlink_calls, (
            "Original video was unlinked before swap — use Path.replace() for crash-safety"
        )
