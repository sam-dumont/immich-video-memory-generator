"""generate.py orchestration: extraction, settings, Live Photo bursts, music and upload."""

from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import (
    GenerationError,
    GenerationParams,
    PipelineLock,
    check_disk_space,
    generate_memory,
)
from immich_memories.generate_clips import (
    assets_to_clips,
    cleanup_temp_clips,
    cleanup_temp_dirs,
    extract_clips,
)
from immich_memories.generate_music import MusicSelection
from immich_memories.generate_settings import build_assembly_settings, build_title_settings
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.rate_control import quality_args
from tests.conftest import make_asset, make_clip
from tests.output_tools_fake import output_tools


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


# ---------------------------------------------------------------------------
# extract_clips
# ---------------------------------------------------------------------------


class TestExtractClips:
    def test_download_failure_skips_clip(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
        )

        # WHY: mock download to simulate network failure
        with patch("immich_memories.generate_downloads.download_clip", return_value=None):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert result == []

    def test_download_returns_nonexistent_path_skips(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
        )

        # WHY: mock download returning a path that doesn't exist on disk
        with patch(
            "immich_memories.generate_downloads.download_clip",
            return_value=tmp_path / "does_not_exist.mp4",
        ):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert result == []

    def test_extract_exception_skips_clip(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
        )

        # WHY: mock download to return a valid file, but extract_clip to fail
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                side_effect=OSError("FFmpeg error"),
            ),
        ):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert result == []

    def test_successful_extraction_builds_assembly_clip(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")
        segment_file = tmp_path / "segment.mp4"
        segment_file.write_bytes(b"segment data")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
        )

        # WHY: mock download and extract to avoid real I/O
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=segment_file,
            ),
        ):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert len(result) == 1
        assert result[0].asset_id == "clip-1"
        assert result[0].path == segment_file

    def test_clip_segments_override_start_end(self, tmp_path):
        clip = make_clip("clip-1", duration=10.0)
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")
        segment_file = tmp_path / "segment.mp4"
        segment_file.write_bytes(b"segment data")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
            clip_segments={"clip-1": (2.0, 7.0)},
        )

        # WHY: mock download and extract to avoid real I/O
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=segment_file,
            ) as mock_extract,
        ):
            mock_cache = MagicMock()
            extract_clips(params, mock_cache, tmp_path)

        # Verify the custom segment bounds were passed to extract_clip
        _, kwargs = mock_extract.call_args
        assert kwargs["start_time"] == 2.0
        assert kwargs["end_time"] == 7.0

    def test_rotation_override_propagates(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")
        segment_file = tmp_path / "segment.mp4"
        segment_file.write_bytes(b"segment data")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
            clip_rotations={"clip-1": 90},
        )

        # WHY: mock download and extract to avoid real I/O
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=segment_file,
            ),
        ):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert result[0].rotation_override == 90

    def test_progress_callback_called_during_extraction(self, tmp_path):
        clip = make_clip("clip-1", duration=5.0)
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")
        segment_file = tmp_path / "segment.mp4"
        segment_file.write_bytes(b"segment data")

        calls: list[tuple[str, float, str]] = []
        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
            progress_callback=lambda p, pct, m: calls.append((p, pct, m)),
        )

        # WHY: mock download and extract to avoid real I/O
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=segment_file,
            ),
        ):
            mock_cache = MagicMock()
            extract_clips(params, mock_cache, tmp_path)

        extract_calls = [c for c in calls if c[0] == "extract"]
        assert len(extract_calls) >= 2  # "Downloading" and "Extracting segment"

    def test_exif_gps_propagated_to_assembly_clip(self, tmp_path):
        from immich_memories.api.models import ExifInfo

        clip = make_clip("clip-1", duration=5.0)
        clip.asset.exif_info = ExifInfo(
            latitude=48.8566, longitude=2.3522, city="Paris", country="France"
        )
        video_file = tmp_path / "video.mp4"
        video_file.write_bytes(b"fake video data")
        segment_file = tmp_path / "segment.mp4"
        segment_file.write_bytes(b"segment data")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "out.mp4",
            config=Config(),
            client=MagicMock(),
        )

        # WHY: mock download and extract to avoid real I/O
        with (
            patch("immich_memories.generate_downloads.download_clip", return_value=video_file),
            patch(
                "immich_memories.processing.clips.extract_clip",
                return_value=segment_file,
            ),
        ):
            mock_cache = MagicMock()
            result = extract_clips(params, mock_cache, tmp_path)

        assert result[0].latitude == 48.8566
        assert result[0].longitude == 2.3522
        assert result[0].location_name == "Paris, France"


# ---------------------------------------------------------------------------
# build_title_settings (extended branch tests)
# ---------------------------------------------------------------------------


class TestBuildTitleSettings:
    def test_disabled_returns_none(self):
        config = Config()
        config.title_screens.enabled = False
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        assert build_title_settings(params, config, []) is None

    def test_trip_memory_type_extracts_locations(self):
        config = Config()
        config.network.map_tiles = True  # the pins only exist for a map
        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0, latitude=48.85, longitude=2.35),
        ]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=config,
            memory_type="trip",
            memory_preset_params={
                "location_name": "Paris",
                "trip_start": date(2025, 7, 1),
                "trip_end": date(2025, 7, 14),
            },
        )
        result = build_title_settings(params, config, clips)
        assert result is not None
        assert result.memory_type == "trip"
        assert result.trip_locations is not None
        assert len(result.trip_locations) == 1

    def test_non_trip_has_no_locations(self):
        config = Config()
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=config,
            memory_type="year_in_review",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        result = build_title_settings(params, config, [])
        assert result.trip_locations is None

    def test_month_dividers_disabled_forces_none_mode(self):
        config = Config()
        config.title_screens.show_month_dividers = False
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=config,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        result = build_title_settings(params, config, [])
        assert result.divider_mode == "none"
        assert result.show_month_dividers is False

    def test_home_lat_lon_from_preset_params(self):
        config = Config()
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=config,
            memory_type="trip",
            memory_preset_params={
                "home_lat": 40.7128,
                "home_lon": -74.0060,
                "location_name": "NYC",
                "trip_start": date(2025, 6, 1),
                "trip_end": date(2025, 6, 10),
            },
        )
        result = build_title_settings(params, config, [])
        assert result.home_lat == 40.7128
        assert result.home_lon == -74.0060


# ---------------------------------------------------------------------------
# assets_to_clips (edge cases)
# ---------------------------------------------------------------------------


class TestAssetsToClipsEdgeCases:
    def test_exactly_at_threshold_is_excluded(self):
        # MIN_CLIP_DURATION = 1.5 — a clip of exactly 1.4s should be excluded
        assets = [make_asset("a1", duration="0:00:01.400")]
        assert assets_to_clips(assets) == []

    def test_at_threshold_is_included(self):
        assets = [make_asset("a1", duration="0:00:01.500")]
        assert len(assets_to_clips(assets)) == 1

    def test_none_duration_treated_as_zero(self):
        asset = make_asset("a1", duration=None)
        assert assets_to_clips([asset]) == []


# ---------------------------------------------------------------------------
# PipelineLock
# ---------------------------------------------------------------------------


class TestPipelineLock:
    def test_acquires_and_releases(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with PipelineLock(lock_path):
            assert lock_path.exists()

    def test_concurrent_lock_raises_generation_error(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with PipelineLock(lock_path), pytest.raises(GenerationError, match="Another instance"):  # noqa: SIM117
            with PipelineLock(lock_path):
                pass  # Should not reach here

    def test_lock_released_after_exit(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        with PipelineLock(lock_path):
            pass
        # Should be able to re-acquire
        with PipelineLock(lock_path):
            pass

    def test_creates_parent_directories(self, tmp_path):
        lock_path = tmp_path / "nested" / "deep" / "test.lock"
        with PipelineLock(lock_path):
            assert lock_path.parent.exists()


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------


class TestCheckDiskSpace:
    def test_raises_on_low_disk_space(self, tmp_path):
        # WHY: mock disk_usage to simulate low disk space without consuming real disk
        with patch("immich_memories.generate.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=500 * 1024 * 1024)  # 500 MB
            with pytest.raises(GenerationError, match="Insufficient disk space"):
                check_disk_space(tmp_path)

    def test_passes_with_sufficient_space(self, tmp_path):
        # WHY: mock disk_usage to simulate sufficient disk space
        with patch("immich_memories.generate.shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(free=5 * 1024 * 1024 * 1024)  # 5 GB
            check_disk_space(tmp_path)  # Should not raise


# ---------------------------------------------------------------------------
# generate_memory top-level
# ---------------------------------------------------------------------------


class TestGenerateMemory:
    def test_no_clips_raises(self):
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=Config())
        with pytest.raises(GenerationError, match="No clips provided"):
            generate_memory(params)


# ---------------------------------------------------------------------------
# cleanup_temp_clips
# ---------------------------------------------------------------------------


class TestCleanupTempClips:
    def test_removes_tmp_files(self, tmp_path):
        tmp_clip = tmp_path / "tmp_segment.mp4"
        tmp_clip.write_bytes(b"data")
        clips = [AssemblyClip(path=tmp_clip, duration=3.0)]
        cleanup_temp_clips(clips)
        assert not tmp_clip.exists()


# ---------------------------------------------------------------------------
# cleanup_temp_dirs
# ---------------------------------------------------------------------------


class TestCleanupTempDirs:
    def test_removes_known_temp_subdirs(self, tmp_path):
        for name in (".title_screens", ".intermediates", "photos"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "file.txt").write_text("x")
        cleanup_temp_dirs(tmp_path)
        assert not (tmp_path / ".title_screens").exists()
        assert not (tmp_path / ".intermediates").exists()
        assert not (tmp_path / "photos").exists()

    def test_preserves_unknown_subdirs(self, tmp_path):
        (tmp_path / "keep_me").mkdir()
        cleanup_temp_dirs(tmp_path)
        assert (tmp_path / "keep_me").exists()


# ---------------------------------------------------------------------------
# build_assembly_settings (extra branches)
# ---------------------------------------------------------------------------


class TestBuildAssemblySettingsExtraBranches:
    def test_smart_transition(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="smart"
        )
        settings = build_assembly_settings(params, [])
        assert settings.transition == TransitionType.SMART

    def test_none_transition(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="none"
        )
        settings = build_assembly_settings(params, [])
        assert settings.transition == TransitionType.NONE

    def test_unknown_transition_defaults_crossfade(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="wipe"
        )
        settings = build_assembly_settings(params, [])
        assert settings.transition == TransitionType.CROSSFADE

    def test_prores_format(self):
        from immich_memories.processing.encoding_plan import OutputCodec

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), output_format="prores"
        )
        settings = build_assembly_settings(params, [])
        assert settings.encoding_plan.codec is OutputCodec.PRORES

    def test_unknown_explicit_format_is_rejected(self):
        config = Config()
        config.output.codec = "h265"
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=config,
            output_format="webm",
        )
        with pytest.raises(ValueError, match="Unsupported format override"):
            build_assembly_settings(params, [])

    def test_scale_mode_from_params(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            scale_mode="fit",
        )
        settings = build_assembly_settings(params, [])
        assert settings.scale_mode == "fit"

    def test_legacy_scale_mode_from_params_is_mapped(self):
        """A caller still passing smart_crop gets blur, not a silent letterbox."""
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            scale_mode="smart_crop",
        )
        settings = build_assembly_settings(params, [])
        assert settings.scale_mode == "blur"

    def test_scale_mode_from_config_when_param_none(self):
        config = Config()
        config.defaults.scale_mode = "fit"
        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=config, scale_mode=None
        )
        settings = build_assembly_settings(params, [])
        assert settings.scale_mode == "fit"

    def test_4k_resolution(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            output_resolution="4k",
        )
        settings = build_assembly_settings(params, [])
        assert settings.target_resolution == (3840, 2160)
        assert settings.auto_resolution is False

    def test_date_overlay_passed(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            add_date_overlay=True,
        )
        settings = build_assembly_settings(params, [])
        assert settings.add_date_overlay is True

    def test_debug_mode_passed(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            debug_preserve_intermediates=True,
        )
        settings = build_assembly_settings(params, [])
        assert settings.debug_preserve_intermediates is True

    def test_crf_from_params(self):
        config = Config()
        config.hardware.enabled = False
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=config,
            output_crf=18,
        )
        settings = build_assembly_settings(params, [])
        args = settings.encoding_plan.encoder_args
        # The requested CRF reaches the encoder translated onto its own scale,
        # and the plan records the reference number it came from.
        assert settings.encoding_plan.crf == 18
        assert args[args.index("-crf") + 1] == quality_args("libx264", 18)[-1]


# ---------------------------------------------------------------------------
# generate_memory — one run end to end, with its media boundaries stubbed
# ---------------------------------------------------------------------------


class TestGenerateMemoryRun:
    def _make_params(self, tmp_path, **overrides):
        clip = make_clip("clip-1", duration=5.0)
        defaults = {
            "clips": [clip],
            "output_path": tmp_path / "output" / "memory.mp4",
            "config": Config(),
            "client": MagicMock(),
            "no_music": True,
        }
        defaults.update(overrides)
        return GenerationParams(**defaults)

    def _patch_inner_deps(self, tmp_path):
        """Patches for every external boundary a generate_memory run crosses."""
        source_path = tmp_path / "result.mp4"
        source_path.write_bytes(b"fake video")
        result_path = tmp_path / "output" / "memory_20250715_120000_abcd" / "memory.mp4"

        assembly_clip = AssemblyClip(
            path=source_path, duration=5.0, asset_id="clip-1", date="2025-07-15"
        )

        def assemble_to_staged(_clips, output_path: Path, _callback, **_kwargs) -> Path:
            output_path.write_bytes(b"fake video")
            return output_path

        mock_assembler = MagicMock()
        mock_assembler.assemble_with_titles.side_effect = assemble_to_staged
        probe_payload = {
            "streams": [
                {
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "color_transfer": "bt709",
                    "color_primaries": "bt709",
                    "width": 1920,
                    "height": 1080,
                    "nb_read_frames": "150",
                }
            ],
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "5.0",
                "size": "10",
                "tags": {"major_brand": "isom"},
            },
        }

        patches = {
            # WHY: VideoDownloadCache hits filesystem for video caching
            "cache": patch(
                "immich_memories.cache.video_cache.VideoDownloadCache",
                return_value=MagicMock(),
            ),
            # WHY: RunTracker writes to SQLite database
            "tracker": patch(
                "immich_memories.tracking.RunTracker",
                return_value=MagicMock(),
            ),
            # WHY: generate_run_id generates unique IDs from OS random
            "run_id": patch(
                "immich_memories.tracking.generate_run_id",
                return_value="20250715_120000_abcd",
            ),
            # WHY: set_current_run_id sets thread-local state
            "set_run_id": patch("immich_memories.logging_config.set_current_run_id"),
            # WHY: check_disk_space calls shutil.disk_usage
            "disk": patch("immich_memories.generate.check_disk_space"),
            # WHY: extract_clips downloads from Immich + runs FFmpeg
            "extract": patch(
                "immich_memories.generate_render.extract_clips",
                return_value=[assembly_clip],
            ),
            # WHY: validate_clips checks file existence on disk
            "validate": patch(
                "immich_memories.generate_render.validate_clips",
                return_value=([assembly_clip], []),
            ),
            # WHY: create_assembler creates VideoAssembler with FFmpeg deps
            "assembler": patch(
                "immich_memories.generate_render.create_assembler",
                return_value=mock_assembler,
            ),
            # WHY: run_music_phase calls external music generation APIs
            "music": patch("immich_memories.generate.run_music_phase"),
            # WHY: ffprobe and ffmpeg read the film; keep the real validation contract.
            "probe": patch(
                "immich_memories.processing.output_contract.subprocess.run",
                side_effect=output_tools(probe_payload),
            ),
            # WHY: sanitize_filename is a security utility
            "sanitize": patch(
                "immich_memories.security.sanitize_filename",
                side_effect=lambda x: x,
            ),
        }
        return patches, result_path, assembly_clip

    def test_happy_path_returns_result_path(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, result_path, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            result = generate_memory(params)

        assert result == result_path

    def test_the_run_targets_the_reviewed_segment_lengths(self, tmp_path):
        clips = [make_clip("clip-1", duration=10.0), make_clip("clip-2", duration=7.5)]
        params = self._make_params(tmp_path, clips=clips, clip_segments={"clip-1": (2.0, 6.0)})
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            generate_memory(params)

        start = mocks["tracker"].return_value.start_run.call_args.kwargs
        assert start["target_duration_seconds"] == 11

    def test_calls_extract_then_assemble_in_order(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, result_path, _ = self._patch_inner_deps(tmp_path)
        call_order = []

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = lambda *_a, **_kw: (  # noqa: ARG005
                call_order.append("extract"),
                [AssemblyClip(path=result_path, duration=5.0, asset_id="c1", date="2025-07-15")],
            )[1]

            def assemble_in_order(_clips, output_path: Path, _callback, **_kwargs) -> Path:
                call_order.append("assemble")
                output_path.write_bytes(b"fake video")
                return output_path

            mocks["assembler"].return_value.assemble_with_titles.side_effect = assemble_in_order
            generate_memory(params)

        assert call_order == ["extract", "assemble"]

    def test_disabled_video_cache_never_constructs_or_mutates_persistent_cache(self, tmp_path):
        """Disabled caching uses disposable downloads, not the configured cache root."""
        persistent_root = tmp_path / "persistent-cache"
        params = self._make_params(
            tmp_path,
            config=Config(
                cache={
                    "directory": str(persistent_root),
                    "database": str(tmp_path / "runs.db"),
                    "video_cache_enabled": False,
                }
            ),
        )
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch) for name, patch in patches.items()}
            generate_memory(params)

        mocks["cache"].assert_not_called()
        assert not params.config.cache.video_cache_path.exists()
        assert mocks["extract"].call_args.args[1] is None

    def test_enabled_video_cache_passes_one_batch_to_extraction(self, tmp_path):
        """Enabled generation owns exactly one batch for all extraction downloads."""
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)
        cache = MagicMock()
        batch = MagicMock()
        cache.begin_batch.return_value.__enter__.return_value = batch

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch) for name, patch in patches.items()}
            mocks["cache"].return_value = cache
            generate_memory(params)

        cache.begin_batch.assert_called_once()
        assert mocks["extract"].call_args.args[1] is batch

    def test_no_clips_after_extraction_raises(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].return_value = []
            mocks["validate"].return_value = ([], [])

            with pytest.raises(GenerationError, match="No clips could be processed"):
                generate_memory(params)

    def test_privacy_mode_anonymizes_clips(self, tmp_path):
        params = self._make_params(tmp_path, privacy_mode=True, person_name="Riley")
        patches, result_path, assembly_clip = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            anon_mock = stack.enter_context(
                patch(
                    "immich_memories.generate_render.anonymize_clips_for_privacy",
                    return_value=[assembly_clip],
                )
            )
            preset_mock = stack.enter_context(
                patch("immich_memories.generate_render.anonymize_preset_params", return_value={})
            )
            name_mock = stack.enter_context(
                patch("immich_memories.generate_render.anonymize_name", return_value="Anon")
            )
            generate_memory(params)

        anon_mock.assert_called_once()
        preset_mock.assert_called_once()
        name_mock.assert_called_once_with("Riley")

    def test_upload_called_when_enabled(self, tmp_path):
        mock_client = MagicMock()
        params = self._make_params(
            tmp_path, upload_enabled=True, upload_album="test-album", client=mock_client
        )
        patches, result_path, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            upload_mock = stack.enter_context(
                patch("immich_memories.generate_delivery.upload_to_immich")
            )
            upload_mock.return_value = {"asset_id": "uploaded-asset"}
            generate_memory(params)

        upload_mock.assert_called_once_with(mock_client, result_path, "test-album", None)
        mocks["tracker"].return_value.mark_delivered.assert_called_once_with("uploaded-asset")

    def test_upload_not_called_when_disabled(self, tmp_path):
        params = self._make_params(tmp_path, upload_enabled=False)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            upload_mock = stack.enter_context(
                patch("immich_memories.generate_delivery.upload_to_immich")
            )
            generate_memory(params)

        upload_mock.assert_not_called()

    def test_unexpected_exception_wrapped_in_generation_error(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = RuntimeError("something broke")

            with pytest.raises(GenerationError, match="Generation failed"):
                generate_memory(params)

    def test_generation_error_not_re_wrapped(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = GenerationError("intentional")

            with pytest.raises(GenerationError, match="intentional"):
                generate_memory(params)

    def test_set_current_run_id_cleared_in_finally(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            generate_memory(params)

        # set_current_run_id called with the run_id first, then None in finally
        calls = mocks["set_run_id"].call_args_list
        assert calls[-1].args == (None,)

    def _intermediates(self, tmp_path) -> Path:
        """A leftover the run's own cleanup is expected to remove."""
        leftover = tmp_path / "output" / "memory_20250715_120000_abcd" / ".intermediates"
        leftover.mkdir(parents=True)
        return leftover

    def test_debug_mode_preserves_intermediates(self, tmp_path):
        params = self._make_params(tmp_path, debug_preserve_intermediates=True)
        patches, _, _ = self._patch_inner_deps(tmp_path)
        leftover = self._intermediates(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            generate_memory(params)

        assert leftover.is_dir()

    def test_a_finished_run_removes_its_intermediates_and_temp_clips(self, tmp_path):
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)
        leftover = self._intermediates(tmp_path)
        temp_clip = tmp_path / "tmp_segment.mp4"
        temp_clip.write_bytes(b"segment")

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            clip = AssemblyClip(path=temp_clip, duration=5.0, asset_id="clip-1", date="2025-07-15")
            mocks["extract"].return_value = [clip]
            mocks["validate"].return_value = ([clip], [])
            generate_memory(params)

        assert not leftover.exists()
        assert not temp_clip.exists()

    def test_cleanup_runs_even_on_error(self, tmp_path):
        """Temp cleanup must run in finally, even when the pipeline fails."""
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)
        leftover = self._intermediates(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = RuntimeError("boom")

            with pytest.raises(GenerationError):
                generate_memory(params)

        assert not leftover.exists()

    def test_cleanup_failure_does_not_mask_pipeline_error(self, tmp_path):
        """If cleanup itself raises, the original pipeline error still propagates."""
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = RuntimeError("pipeline broke")
            # WHY: a disk that refuses the delete is the failure this test is about
            stack.enter_context(
                patch(
                    "immich_memories.generate.cleanup_temp_clips",
                    side_effect=OSError("cleanup also broke"),
                )
            )
            # WHY: same refusal for the intermediates directory
            stack.enter_context(
                patch(
                    "immich_memories.generate.cleanup_temp_dirs",
                    side_effect=OSError("dir cleanup broke"),
                )
            )

            with pytest.raises(GenerationError, match="pipeline broke"):
                generate_memory(params)

    def test_fail_run_called_on_generation_error(self, tmp_path):
        """fail_run() is called when GenerationError is raised."""
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = GenerationError("intentional")
            mocks["tracker"].return_value.db.get_run.return_value.status = "running"

            with pytest.raises(GenerationError):
                generate_memory(params)

        mocks["tracker"].return_value.fail_run.assert_called_once()

    def test_fail_run_called_on_unexpected_error(self, tmp_path):
        """fail_run() is called when unexpected exception is raised."""
        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = RuntimeError("surprise")
            mocks["tracker"].return_value.db.get_run.return_value.status = "running"

            with pytest.raises(GenerationError):
                generate_memory(params)

        mocks["tracker"].return_value.fail_run.assert_called_once()


# ---------------------------------------------------------------------------
# generate_memory: one run at a time
# ---------------------------------------------------------------------------


def test_a_second_run_is_refused_while_one_holds_the_lock(tmp_path):
    params = GenerationParams(
        clips=[make_clip("c1", duration=5.0)],
        output_path=tmp_path / "output.mp4",
        config=Config(cache={"database": str(tmp_path / "db" / "runs.db")}),
    )

    with (
        PipelineLock(tmp_path / "db" / ".lock"),
        pytest.raises(GenerationError, match="Another instance"),
    ):
        generate_memory(params)

    assert not (tmp_path / "output.mp4").exists()


# ---------------------------------------------------------------------------
# run_music_phase
# ---------------------------------------------------------------------------


class TestRunMusicPhase:
    def _run(self, tmp_path, params, **resolve):
        from immich_memories.generate_settings import run_music_phase

        tracker = MagicMock()
        # WHY: resolve_music generates or reads a soundtrack; its outcome is the input here
        with patch("immich_memories.generate_music.resolve_music", **resolve):
            result = run_music_phase(
                params, [], tmp_path / "r.mp4", tmp_path, tracker, encoding_plan=_h264_output_plan()
            )
        return result, tracker

    def test_an_explicit_music_path_is_tracked_as_music_work(self, tmp_path):
        params = GenerationParams(
            clips=[], output_path=tmp_path / "o.mp4", config=Config(), music_path=tmp_path / "m.wav"
        )

        _, tracker = self._run(tmp_path, params, return_value=MusicSelection(None))

        tracker.start_phase.assert_called_once_with("music", 1)

    def test_no_music_wins_over_a_path_and_a_configured_backend(self, tmp_path):
        config = Config()
        config.ace_step.enabled = True
        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "o.mp4",
            config=config,
            music_path=tmp_path / "m.wav",
            no_music=True,
        )

        _, tracker = self._run(tmp_path, params, return_value=MusicSelection(None))

        tracker.start_phase.assert_not_called()

    def test_a_failing_music_backend_is_an_optional_warning(self, tmp_path):
        params = GenerationParams(clips=[], output_path=tmp_path / "o.mp4", config=Config())

        result, tracker = self._run(
            tmp_path, params, side_effect=RuntimeError("backend unavailable")
        )

        assert result.applied is False
        assert result.warning == "Optional music failed: backend unavailable"
        tracker.start_phase.assert_called_once_with("music", 1)
        tracker.complete_phase.assert_called_once_with(
            items_processed=0,
            errors=[{"error": "Optional music failed: backend unavailable"}],
        )

    def test_skips_when_no_music_resolved(self, tmp_path):
        from immich_memories.generate_settings import run_music_phase

        params = GenerationParams(
            clips=[], output_path=Path("/tmp/o.mp4"), config=Config(), no_music=True
        )
        mock_tracker = MagicMock()

        # WHY: resolve_music checks filesystem for music files
        with patch(
            "immich_memories.generate_music.resolve_music",
            return_value=MusicSelection(None),
        ) as mock_resolve:
            run_music_phase(
                params,
                [],
                tmp_path / "result.mp4",
                tmp_path,
                mock_tracker,
                encoding_plan=_h264_output_plan(),
            )

        mock_resolve.assert_called_once()
        mock_tracker.start_phase.assert_not_called()

    def test_applies_music_when_resolved(self, tmp_path):
        from immich_memories.generate_settings import run_music_phase

        music_file = tmp_path / "music.mp3"
        music_file.write_bytes(b"music")
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"video")

        params = GenerationParams(
            clips=[], output_path=Path("/tmp/o.mp4"), config=Config(), music_volume=0.7
        )
        mock_tracker = MagicMock()
        encoding_plan = _h264_output_plan()

        # WHY: resolve_music and apply_music_file touch filesystem + FFmpeg
        with (
            patch(
                "immich_memories.generate_music.resolve_music",
                return_value=MusicSelection(music_file),
            ),
            patch("immich_memories.generate_music.apply_music_file") as mock_apply,
        ):
            run_music_phase(
                params,
                [],
                result_path,
                tmp_path,
                mock_tracker,
                encoding_plan=encoding_plan,
            )

        mock_apply.assert_called_once_with(
            result_path,
            music_file,
            0.7,
            encoding_plan,
            mute_windows=None,
            stems=None,
        )
        mock_tracker.start_phase.assert_called_once_with("music", 1)
        mock_tracker.complete_phase.assert_called_once_with(items_processed=1)

    def test_starts_music_phase_before_resolving_generated_music(self, tmp_path):
        from immich_memories.generate_settings import run_music_phase

        music_file = tmp_path / "music.wav"
        music_file.write_bytes(b"music")
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"video")
        config = Config()
        config.ace_step.enabled = True
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        events: list[str] = []
        mock_tracker = MagicMock()
        mock_tracker.start_phase.side_effect = lambda *_args: events.append("phase-start")

        def record_resolve(**_kwargs):
            events.append("resolve")
            return MusicSelection(music_file)

        # WHY: resolve_music and apply_music_file would hit disk, FFmpeg, and music APIs.
        with (
            # WHY: resolve_music can shell out to MusicGen/ACE-Step and read the bundled library.
            patch(
                "immich_memories.generate_music.resolve_music",
                side_effect=record_resolve,
            ),
            patch("immich_memories.generate_music.apply_music_file"),
        ):
            run_music_phase(
                params,
                [],
                result_path,
                tmp_path,
                mock_tracker,
                encoding_plan=_h264_output_plan(),
            )

        assert events[:2] == ["phase-start", "resolve"]

    def test_report_fn_delegates_to_progress_callback(self, tmp_path):
        from immich_memories.generate_settings import run_music_phase

        calls = []
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            progress_callback=lambda p, pct, m: calls.append((p, pct, m)),
        )
        mock_tracker = MagicMock()

        # WHY: resolve_music accesses the filesystem
        with patch(
            "immich_memories.generate_music.resolve_music",
            return_value=MusicSelection(None),
        ) as mock_resolve:
            run_music_phase(
                params,
                [],
                tmp_path / "r.mp4",
                tmp_path,
                mock_tracker,
                encoding_plan=_h264_output_plan(),
            )

        # Verify resolve_music received a report_fn callback
        call_kwargs = mock_resolve.call_args
        assert (
            call_kwargs.kwargs.get("report_fn") is not None
            or call_kwargs[1].get("report_fn") is not None
        )


# ---------------------------------------------------------------------------
# upload_to_immich
# ---------------------------------------------------------------------------


class TestUploadToImmich:
    def test_calls_client_upload(self, tmp_path):
        from immich_memories.generate_settings import upload_to_immich

        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        # WHY: SyncImmichClient.upload_memory hits the Immich REST API
        mock_client = MagicMock()
        mock_client.upload_memory.return_value = {"asset_id": "abc123"}

        result = upload_to_immich(mock_client, video_path, "My Album")

        mock_client.upload_memory.assert_called_once_with(
            video_path=video_path, album_name="My Album", captured_at=None
        )
        assert result["asset_id"] == "abc123"

    def test_none_album_name(self, tmp_path):
        from immich_memories.generate_settings import upload_to_immich

        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        mock_client = MagicMock()
        mock_client.upload_memory.return_value = {}

        upload_to_immich(mock_client, video_path, None)
        mock_client.upload_memory.assert_called_once_with(
            video_path=video_path, album_name=None, captured_at=None
        )


# ---------------------------------------------------------------------------
# _apply_unified_budget
# ---------------------------------------------------------------------------


class TestDownloadClip:
    def test_local_path_exists_skips_download(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        local_file = tmp_path / "local.mp4"
        local_file.write_bytes(b"data")

        clip = MagicMock(editorial_live_manifest=None)
        clip.local_path = str(local_file)

        result = download_clip(MagicMock(), MagicMock(), clip, tmp_path)
        assert result == local_file

    def test_local_path_nonexistent_proceeds_to_download(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock(editorial_live_manifest=None)
        clip.local_path = str(tmp_path / "nonexistent.mp4")
        clip.live_burst_video_ids = None
        clip.live_burst_trim_points = None

        mock_cache = MagicMock()
        cached_path = tmp_path / "cached.mp4"
        mock_cache.download_or_get.return_value = cached_path

        # WHY: SyncImmichClient downloads from Immich REST API
        result = download_clip(MagicMock(), mock_cache, clip, tmp_path)
        assert result == cached_path

    def test_none_client_returns_none(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock(editorial_live_manifest=None)
        clip.local_path = None

        result = download_clip(None, MagicMock(), clip, tmp_path)
        assert result is None

    def test_no_local_path_no_burst_uses_cache(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock(editorial_live_manifest=None)
        clip.local_path = None
        clip.live_burst_video_ids = None
        clip.live_burst_trim_points = None

        cached = tmp_path / "cached.mp4"
        mock_cache = MagicMock()
        mock_cache.download_or_get.return_value = cached

        result = download_clip(MagicMock(), mock_cache, clip, tmp_path)
        assert result == cached


# ===========================================================================
# generate_music.py
# ===========================================================================


class TestResolveMusic:
    def test_no_music_flag_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music

        result = resolve_music(
            config=Config(),
            music_path=None,
            no_music=True,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
            transition_overlap=0.0,
        )
        assert result.path is None

    def test_explicit_music_path_returned(self, tmp_path):
        from immich_memories.generate_music import resolve_music

        music = tmp_path / "song.mp3"
        music.write_bytes(b"audio")
        result = resolve_music(
            config=Config(),
            music_path=music,
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
            transition_overlap=0.0,
        )
        assert result.path == music

    def test_explicit_path_nonexistent_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music

        result = resolve_music(
            config=Config(),
            music_path=tmp_path / "nonexistent.mp3",
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
            transition_overlap=0.0,
        )
        assert result.path is None

    def test_auto_generate_when_no_path_and_config_available(self, tmp_path):
        from immich_memories.audio.music_generator_models import GeneratedMusic
        from immich_memories.generate_music import resolve_music

        config = Config()
        calls = []

        # WHY: music_config_available checks external music generation services
        with (
            patch("immich_memories.generate_music.music_config_available", return_value=True),
            patch(
                "immich_memories.generate_music.auto_generate_music",
                return_value=GeneratedMusic(full_mix=tmp_path / "generated.mp3"),
            ) as mock_gen,
        ):

            def report_fn(p, pct, m):
                return calls.append((p, pct, m))

            result = resolve_music(
                config=config,
                music_path=None,
                no_music=False,
                assembly_clips=[],
                run_output_dir=tmp_path,
                memory_type="month",
                report_fn=report_fn,
                transition_overlap=0.0,
            )

        mock_gen.assert_called_once()
        assert result.path == tmp_path / "generated.mp3"
        # report_fn should have been called with music progress
        assert any(c[0] == "music" for c in calls)

    def test_no_path_no_config_and_no_bundle_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music

        # WHY: music_config_available checks external service configs
        with patch("immich_memories.generate_music.music_config_available", return_value=False):
            result = resolve_music(
                config=Config(),
                music_path=None,
                no_music=False,
                assembly_clips=[],
                run_output_dir=tmp_path,
                memory_type=None,
                bundled_library=tmp_path / "no-bundle",
                transition_overlap=0.0,
            )
        # Silent only when there is no bundled music to fall back to (#308).
        assert result.path is None


class TestAutoGenerateMusic:
    def test_no_config_returns_none(self, tmp_path):
        from immich_memories.generate_music import auto_generate_music

        config = Config()
        # WHY: music_config_available checks MusicGen/ACE-Step service configs
        with patch("immich_memories.generate_music.music_config_available", return_value=False):
            result = auto_generate_music(config, [], tmp_path, None, transition_overlap=0.0)
        assert result is None

    def test_generation_exception_reaches_optional_phase_boundary(self, tmp_path):
        from immich_memories.generate_music import auto_generate_music

        config = Config()
        # WHY: music_config_available and generate_music_for_video would call real config/APIs.
        with (
            # WHY: music_config_available is forced True so the optional-music branch is taken.
            patch("immich_memories.generate_music.music_config_available", return_value=True),
            # WHY: generate_music_for_video calls external MusicGen/ACE-Step APIs
            patch(
                "immich_memories.audio.music_generator.generate_music_for_video",
                side_effect=RuntimeError("API down"),
            ),
            pytest.raises(RuntimeError, match="API down"),
        ):
            auto_generate_music(config, [], tmp_path, "month", transition_overlap=0.0)


class TestMusicConfigAvailable:
    def test_ace_step_enabled(self):
        from immich_memories.generate_music import music_config_available

        config = MagicMock()
        config.ace_step.enabled = True
        config.musicgen = None
        assert music_config_available(config) is True

    def test_musicgen_enabled(self):
        from immich_memories.generate_music import music_config_available

        config = MagicMock()
        config.ace_step = None
        config.musicgen.enabled = True
        assert music_config_available(config) is True

    def test_nothing_enabled(self):
        from immich_memories.generate_music import music_config_available

        config = MagicMock()
        config.ace_step = None
        config.musicgen = None
        assert music_config_available(config) is False


class TestTitleStyleSwitchesReachTheRenderer:
    """title_screens.animated_background / show_decorative_lines must travel app config → renderer."""

    def test_settings_carry_style_switches(self):
        config = Config()
        config.title_screens.animated_background = False
        config.title_screens.show_decorative_lines = True
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        result = build_title_settings(params, config, [])
        assert result is not None
        assert result.animated_background is False
        assert result.show_decorative_lines is True

    def test_title_config_honors_settings(self):
        from unittest.mock import MagicMock

        from immich_memories.processing.title_inserter import TitleInserter

        # WHY: mock prober — it probes video files via FFmpeg subprocess
        inserter = TitleInserter(settings=MagicMock(), prober=MagicMock())
        config = Config()
        config.title_screens.animated_background = False
        config.title_screens.show_decorative_lines = True
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        settings = build_title_settings(params, config, [])
        assert settings is not None
        title_config = inserter._build_title_config(
            title_settings=settings, target_w=1920, target_h=1080, fps=30
        )
        assert title_config.animated_background is False
        assert title_config.show_decorative_lines is True
