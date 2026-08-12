"""Tests for the generate_memory() orchestrator and helpers."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _h264_output_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _final_probe_payload(*, codec: str = "h264") -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "360",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.0",
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


def test_cli_generate_passes_configured_api_version_to_client(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from immich_memories.api.compatibility import ApiVersionPolicy
    from immich_memories.cli import main

    config = Config(
        immich={
            "url": "https://immich.example.com",
            "api_key": "test-api-key",
            "api_version": "v2",
        }
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with (
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client) as client_factory,
        patch(
            "immich_memories.cli.generate.fetch_videos_and_live_photos",
            return_value=([], []),
        ),
    ):
        result = CliRunner().invoke(
            main,
            [
                "generate",
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-31",
                "--no-music",
                "--output",
                str(tmp_path / "memory.mp4"),
            ],
        )

    assert result.exit_code == 1
    client_factory.assert_called_once_with(
        base_url="https://immich.example.com",
        api_key="test-api-key",
        api_version=ApiVersionPolicy.V2,
    )


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


def test_direct_generation_normalizes_staged_and_final_paths_to_plan_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct caller's stale suffix cannot override the resolved output contract."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    config = Config(
        cache={
            "directory": str(tmp_path / "cache"),
            "database": str(tmp_path / "runs.db"),
        }
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        no_music=True,
    )
    assembled_paths: list[Path] = []
    encoding_plan = EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-profile:v", "3"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )

    class Assembler:
        def assemble_with_titles(
            self,
            _clips,
            output_path: Path,
            _progress_callback,
            **_kwargs,
        ) -> Path:
            assembled_paths.append(output_path)
            output_path.write_bytes(b"assembled-video")
            return output_path

    probe_payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "prores",
                "pix_fmt": "yuv422p10le",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "360",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.0",
            "size": "4096",
            "tags": {"major_brand": "qt  "},
        },
    }

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(probe_payload), "")

    monkeypatch.setattr(output_contract.subprocess, "run", run_probe)
    tracker = MagicMock()
    video_cache = MagicMock()
    with (
        patch("immich_memories.tracking.RunTracker", return_value=tracker),
        patch(
            "immich_memories.cache.video_cache.VideoDownloadCache",
            return_value=video_cache,
        ),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=encoding_plan),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch.object(generate_module, "_run_music_phase"),
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        result = generate_memory(params)

    assert assembled_paths[0].name == "memory.assembling.mov"
    assert result.name == "memory.mov"
    assert result.read_bytes() == b"assembled-video"
    assert not assembled_paths[0].exists()


def test_generation_validation_failure_preserves_old_final_and_stops_downstream_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid assembly must fail before music, upload, or successful run completion."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    config = Config(
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")}
    )
    client = MagicMock()
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        client=client,
        no_music=True,
        upload_enabled=True,
    )
    run_output_dir = tmp_path / "memory_fixed-run"
    run_output_dir.mkdir()
    old_final = run_output_dir / "memory.mp4"
    old_final.write_bytes(b"previous-valid-memory")

    class WrongCodecAssembler:
        def assemble_with_titles(self, _clips, output_path: Path, _callback, **_kwargs) -> Path:
            output_path.write_bytes(b"new-wrong-codec")
            return output_path

    def run_probe(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload(codec="hevc")), ""
        )

    monkeypatch.setattr(output_contract.subprocess, "run", run_probe)
    tracker = MagicMock()
    tracker.db.get_run.return_value.status = "running"
    music_phase = MagicMock()
    upload = MagicMock()
    with (
        pytest.raises(GenerationError, match="expected h264, got hevc"),
        patch("immich_memories.tracking.generate_run_id", return_value="fixed-run"),
        patch("immich_memories.tracking.RunTracker", return_value=tracker),
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=_h264_output_plan()),
        ),
        patch.object(generate_module, "_create_assembler", return_value=WrongCodecAssembler()),
        patch.object(generate_module, "_run_music_phase", music_phase),
        patch.object(generate_module, "_upload_to_immich", upload),
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        generate_memory(params)

    assert old_final.read_bytes() == b"previous-valid-memory"
    assert (run_output_dir / "memory.assembling.mp4").exists()
    music_phase.assert_not_called()
    upload.assert_not_called()
    tracker.complete_artifact.assert_not_called()
    tracker.complete_run.assert_not_called()
    tracker.fail_run.assert_called_once()


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
            "immich_memories.audio.music_generator.generate_music_for_video",
            new_callable=AsyncMock,
        ) as mock_generate:
            from immich_memories.audio.music_generator_models import (
                GeneratedMusic,
                MusicGenerationResult,
                VideoTimeline,
            )

            fake_music_path = tmp_path / "music.wav"
            fake_music_path.write_bytes(b"fake audio")
            mock_generate.return_value = MusicGenerationResult(
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

    def test_auto_music_propagates_backend_failure_to_optional_phase(self, tmp_path):
        """Backend failures reach the optional phase boundary for sanitization."""
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
        with (
            patch(
                "immich_memories.audio.music_generator.generate_music_for_video",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API unreachable"),
            ),
            pytest.raises(RuntimeError, match="API unreachable"),
        ):
            auto_generate_music(
                params.config, assembly_clips, tmp_path / "run_output", params.memory_type
            )


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
