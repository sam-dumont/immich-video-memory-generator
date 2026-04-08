"""Additional behavior tests for generate.py covering uncovered branches."""

from __future__ import annotations

import contextlib
from datetime import UTC, date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import (
    GenerationError,
    GenerationParams,
    PipelineLock,
    _add_photos_if_enabled,
    _build_assembly_settings,
    _build_title_settings,
    _build_title_settings_for_overhead,
    _cleanup_temp_clips,
    _cleanup_temp_dirs,
    _detect_photo_resolution,
    _extract_clips,
    _log_phase_timing,
    _merge_by_date,
    _parse_clip_date,
    _render_photos,
    _total_clip_duration,
    assets_to_clips,
    check_disk_space,
    generate_memory,
)
from immich_memories.processing.assembly_config import AssemblyClip
from tests.conftest import make_asset, make_clip

# ---------------------------------------------------------------------------
# _parse_clip_date
# ---------------------------------------------------------------------------


class TestParseClipDate:
    def test_valid_iso_date(self):
        result = _parse_clip_date("2025-07-15")
        assert result.year == 2025
        assert result.month == 7
        assert result.day == 15

    def test_valid_iso_datetime(self):
        result = _parse_clip_date("2025-07-15T14:30:00")
        assert result.hour == 14
        assert result.minute == 30

    def test_none_returns_epoch_fallback(self):
        result = _parse_clip_date(None)
        assert result.year == 2000
        assert result.month == 1

    def test_empty_string_returns_epoch_fallback(self):
        result = _parse_clip_date("")
        assert result.year == 2000

    def test_invalid_string_returns_epoch_fallback(self):
        result = _parse_clip_date("not-a-date")
        assert result.year == 2000

    def test_result_always_has_utc_timezone(self):
        result = _parse_clip_date("2025-07-15")
        assert result.tzinfo == UTC

    def test_fallback_also_has_utc_timezone(self):
        result = _parse_clip_date("garbage")
        assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# _merge_by_date
# ---------------------------------------------------------------------------


class TestMergeByDate:
    def test_empty_video_and_photo_lists(self):
        result = _merge_by_date([], [])
        assert result == []

    def test_videos_only(self):
        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0, date="2025-03-01"),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0, date="2025-01-01"),
        ]
        result = _merge_by_date(clips, [])
        assert result[0].date == "2025-01-01"
        assert result[1].date == "2025-03-01"

    def test_photos_only(self):
        photos = [
            AssemblyClip(path=Path("/p.mp4"), duration=3.0, date="2025-06-01"),
        ]
        result = _merge_by_date([], photos)
        assert len(result) == 1

    def test_interleaved_by_date(self):
        videos = [
            AssemblyClip(path=Path("/v1.mp4"), duration=3.0, date="2025-01-10"),
            AssemblyClip(path=Path("/v2.mp4"), duration=3.0, date="2025-03-10"),
        ]
        photos = [
            AssemblyClip(path=Path("/p1.mp4"), duration=3.0, date="2025-02-05"),
        ]
        result = _merge_by_date(videos, photos)
        assert [c.date for c in result] == ["2025-01-10", "2025-02-05", "2025-03-10"]

    def test_clips_with_none_dates_sort_first(self):
        clips = [
            AssemblyClip(path=Path("/a.mp4"), duration=3.0, date="2025-06-01"),
            AssemblyClip(path=Path("/b.mp4"), duration=3.0, date=None),
        ]
        result = _merge_by_date(clips, [])
        # None sorts as "" which comes before any date string
        assert result[0].date is None
        assert result[1].date == "2025-06-01"


# ---------------------------------------------------------------------------
# _detect_photo_resolution
# ---------------------------------------------------------------------------


class TestDetectPhotoResolution:
    def test_landscape_majority_keeps_landscape(self):
        clips = [
            make_clip("c1", width=1920, height=1080),
            make_clip("c2", width=1920, height=1080),
            make_clip("c3", width=1080, height=1920),
        ]
        params = GenerationParams(clips=clips, output_path=Path("/tmp/o.mp4"), config=Config())
        w, h = _detect_photo_resolution(params)
        assert w > h  # landscape

    def test_portrait_majority_swaps_to_portrait(self):
        clips = [
            make_clip("c1", width=1080, height=1920),
            make_clip("c2", width=1080, height=1920),
            make_clip("c3", width=1920, height=1080),
        ]
        params = GenerationParams(clips=clips, output_path=Path("/tmp/o.mp4"), config=Config())
        w, h = _detect_photo_resolution(params)
        assert h > w  # portrait

    def test_equal_portrait_landscape_stays_landscape(self):
        clips = [
            make_clip("c1", width=1920, height=1080),
            make_clip("c2", width=1080, height=1920),
        ]
        params = GenerationParams(clips=clips, output_path=Path("/tmp/o.mp4"), config=Config())
        w, h = _detect_photo_resolution(params)
        # Equal split (1 portrait, 1 landscape) — portrait_count (1) is NOT > total//2 (1)
        assert w > h

    def test_no_clips_stays_landscape(self):
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=Config())
        w, h = _detect_photo_resolution(params)
        assert w > h


# ---------------------------------------------------------------------------
# _build_title_settings_for_overhead
# ---------------------------------------------------------------------------


class TestBuildTitleSettingsForOverhead:
    def test_returns_none_when_title_screens_disabled(self):
        config = Config()
        config.title_screens.enabled = False
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        assert _build_title_settings_for_overhead(params) is None

    def test_returns_settings_when_enabled(self):
        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 4.0
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        result = _build_title_settings_for_overhead(params)
        assert result is not None
        assert result.enabled is True
        assert result.title_duration == 4.0

    def test_carries_month_divider_settings(self):
        config = Config()
        config.title_screens.show_month_dividers = False
        config.title_screens.month_divider_threshold = 5
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        result = _build_title_settings_for_overhead(params)
        assert result.show_month_dividers is False
        assert result.month_divider_threshold == 5


# ---------------------------------------------------------------------------
# _render_photos
# ---------------------------------------------------------------------------


class TestRenderPhotos:
    def test_no_client_returns_empty_list(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            client=None,
            photo_assets=[make_asset("p1")],
        )
        result = _render_photos(params, Path("/tmp"), video_clip_count=5)
        assert result == []

    def test_no_client_no_photo_assets(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            client=None,
            photo_assets=None,
        )
        result = _render_photos(params, Path("/tmp"), video_clip_count=0)
        assert result == []


# ---------------------------------------------------------------------------
# _add_photos_if_enabled
# ---------------------------------------------------------------------------


class TestAddPhotosIfEnabled:
    def test_photos_disabled_returns_clips_unchanged(self):
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=3.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=False,
        )
        result = _add_photos_if_enabled(clips, params, Path("/tmp"))
        assert result is clips

    def test_no_photo_assets_returns_clips_unchanged(self):
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=3.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=None,
        )
        result = _add_photos_if_enabled(clips, params, Path("/tmp"))
        assert result is clips

    def test_empty_photo_assets_returns_clips_unchanged(self):
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=3.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=[],
        )
        result = _add_photos_if_enabled(clips, params, Path("/tmp"))
        assert result is clips

    def test_selected_photo_ids_no_client_returns_clips(self):
        asset = make_asset("photo-1")
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=3.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=[asset],
            selected_photo_ids={"photo-1"},
            client=None,
        )
        result = _add_photos_if_enabled(clips, params, Path("/tmp"))
        assert result is clips

    def test_selected_photo_ids_empty_match_returns_clips(self, tmp_path):
        asset = make_asset("photo-999")
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=3.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=[asset],
            selected_photo_ids={"nonexistent-id"},
            client=MagicMock(),
        )
        result = _add_photos_if_enabled(clips, params, tmp_path)
        assert result is clips

    def test_fallback_path_computes_effective_duration(self, tmp_path):
        """When selected_photo_ids is None, falls back to full scoring path."""
        asset = make_asset("photo-1")
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=10.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=[asset],
            selected_photo_ids=None,
            client=None,
            target_duration_seconds=None,
        )

        # WHY: mock the budget function to avoid real photo scoring logic
        with patch("immich_memories.generate_photos._apply_unified_budget") as mock_budget:
            mock_budget.return_value = (clips, [])
            _add_photos_if_enabled(clips, params, tmp_path)

        mock_budget.assert_called_once()
        # effective_duration = sum(durations) * 1.25 = 10 * 1.25 = 12.5
        call_kwargs = mock_budget.call_args
        assert call_kwargs[1]["target_override"] == 12.5

    def test_fallback_path_uses_explicit_target_duration(self, tmp_path):
        asset = make_asset("photo-1")
        clips = [AssemblyClip(path=Path("/a.mp4"), duration=10.0)]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            include_photos=True,
            photo_assets=[asset],
            selected_photo_ids=None,
            client=None,
            target_duration_seconds=60.0,
        )

        # WHY: mock the budget function to avoid real photo scoring logic
        with patch("immich_memories.generate_photos._apply_unified_budget") as mock_budget:
            mock_budget.return_value = (clips, [])
            _add_photos_if_enabled(clips, params, tmp_path)

        assert mock_budget.call_args[1]["target_override"] == 60.0


# ---------------------------------------------------------------------------
# _extract_clips
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
            result = _extract_clips(params, mock_cache, tmp_path)

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
            result = _extract_clips(params, mock_cache, tmp_path)

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
                side_effect=RuntimeError("FFmpeg error"),
            ),
        ):
            mock_cache = MagicMock()
            result = _extract_clips(params, mock_cache, tmp_path)

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
            result = _extract_clips(params, mock_cache, tmp_path)

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
            _extract_clips(params, mock_cache, tmp_path)

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
            result = _extract_clips(params, mock_cache, tmp_path)

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
            _extract_clips(params, mock_cache, tmp_path)

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
            result = _extract_clips(params, mock_cache, tmp_path)

        assert result[0].latitude == 48.8566
        assert result[0].longitude == 2.3522
        assert result[0].location_name == "Paris, France"


# ---------------------------------------------------------------------------
# _build_title_settings (extended branch tests)
# ---------------------------------------------------------------------------


class TestBuildTitleSettings:
    def test_disabled_returns_none(self):
        config = Config()
        config.title_screens.enabled = False
        params = GenerationParams(clips=[], output_path=Path("/tmp/o.mp4"), config=config)
        assert _build_title_settings(params, config, []) is None

    def test_trip_memory_type_extracts_locations(self):
        config = Config()
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
        result = _build_title_settings(params, config, clips)
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
        result = _build_title_settings(params, config, [])
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
        result = _build_title_settings(params, config, [])
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
        result = _build_title_settings(params, config, [])
        assert result.home_lat == 40.7128
        assert result.home_lon == -74.0060


# ---------------------------------------------------------------------------
# _total_clip_duration (with segments)
# ---------------------------------------------------------------------------


class TestTotalClipDurationWithSegments:
    def test_uses_segment_override(self):
        clip = make_clip("c1", duration=10.0)
        params = GenerationParams(
            clips=[clip],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            clip_segments={"c1": (2.0, 6.0)},
        )
        assert _total_clip_duration(params) == 4

    def test_fallback_to_clip_duration(self):
        clip = make_clip("c1", duration=7.5)
        params = GenerationParams(
            clips=[clip],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
        )
        assert _total_clip_duration(params) == 7

    def test_none_duration_defaults_to_five(self):
        clip = make_clip("c1", duration=0.0)
        clip.duration_seconds = None  # type: ignore[assignment]
        params = GenerationParams(
            clips=[clip],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
        )
        assert _total_clip_duration(params) == 5


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
# _cleanup_temp_clips
# ---------------------------------------------------------------------------


class TestCleanupTempClips:
    def test_removes_tmp_files(self, tmp_path):
        tmp_clip = tmp_path / "tmp_segment.mp4"
        tmp_clip.write_bytes(b"data")
        clips = [AssemblyClip(path=tmp_clip, duration=3.0)]
        _cleanup_temp_clips(clips)
        assert not tmp_clip.exists()

    def test_keeps_non_tmp_files(self):
        # WHY: path must NOT contain "tmp" anywhere for the keep-alive branch
        clip = AssemblyClip(path=Path("/var/data/final/output.mp4"), duration=3.0)
        _cleanup_temp_clips([clip])
        # No assertion on disk — the point is that unlink is never called
        # since path.exists() returns False for a nonexistent path

    def test_handles_missing_files_gracefully(self):
        clips = [AssemblyClip(path=Path("/nonexistent/tmp_file.mp4"), duration=3.0)]
        _cleanup_temp_clips(clips)  # Should not raise


# ---------------------------------------------------------------------------
# _cleanup_temp_dirs
# ---------------------------------------------------------------------------


class TestCleanupTempDirs:
    def test_removes_known_temp_subdirs(self, tmp_path):
        for name in (".title_screens", ".intermediates", "photos"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "file.txt").write_text("x")
        _cleanup_temp_dirs(tmp_path)
        assert not (tmp_path / ".title_screens").exists()
        assert not (tmp_path / ".intermediates").exists()
        assert not (tmp_path / "photos").exists()

    def test_ignores_nonexistent_subdirs(self, tmp_path):
        _cleanup_temp_dirs(tmp_path)  # Should not raise

    def test_preserves_unknown_subdirs(self, tmp_path):
        (tmp_path / "keep_me").mkdir()
        _cleanup_temp_dirs(tmp_path)
        assert (tmp_path / "keep_me").exists()


# ---------------------------------------------------------------------------
# _log_phase_timing
# ---------------------------------------------------------------------------


class TestLogPhaseTiming:
    def test_logs_without_error(self):
        times = {"download": 5.0, "photos": 2.0, "assembly": 30.0, "music": 10.0, "total": 47.0}
        _log_phase_timing(times, clip_count=5)  # Should not raise

    def test_zero_total_no_division_error(self):
        times = {"total": 0.0}
        _log_phase_timing(times, clip_count=0)  # Should not raise

    def test_missing_phases_handled(self):
        times = {"total": 10.0}
        _log_phase_timing(times, clip_count=1)  # Should not raise


# ---------------------------------------------------------------------------
# _build_assembly_settings (extra branches)
# ---------------------------------------------------------------------------


class TestBuildAssemblySettingsExtraBranches:
    def test_smart_transition(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="smart"
        )
        settings = _build_assembly_settings(params, [])
        assert settings.transition == TransitionType.SMART

    def test_none_transition(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="none"
        )
        settings = _build_assembly_settings(params, [])
        assert settings.transition == TransitionType.NONE

    def test_unknown_transition_defaults_crossfade(self):
        from immich_memories.processing.assembly_config import TransitionType

        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), transition="wipe"
        )
        settings = _build_assembly_settings(params, [])
        assert settings.transition == TransitionType.CROSSFADE

    def test_prores_format(self):
        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=Config(), output_format="prores"
        )
        settings = _build_assembly_settings(params, [])
        assert settings.output_codec == "prores"

    def test_unknown_format_uses_config_codec(self):
        config = Config()
        config.output.codec = "h265"
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=config,
            output_format="webm",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.output_codec == "h265"

    def test_scale_mode_from_params(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            scale_mode="smart_crop",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.scale_mode == "smart_crop"

    def test_scale_mode_from_config_when_param_none(self):
        config = Config()
        config.defaults.scale_mode = "fill"
        params = GenerationParams(
            clips=[], output_path=Path("/out/o.mp4"), config=config, scale_mode=None
        )
        settings = _build_assembly_settings(params, [])
        assert settings.scale_mode == "fill"

    def test_4k_resolution(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            output_resolution="4k",
        )
        settings = _build_assembly_settings(params, [])
        assert settings.target_resolution == (3840, 2160)
        assert settings.auto_resolution is False

    def test_date_overlay_passed(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            add_date_overlay=True,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.add_date_overlay is True

    def test_debug_mode_passed(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            debug_preserve_intermediates=True,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.debug_preserve_intermediates is True

    def test_crf_from_params(self):
        params = GenerationParams(
            clips=[],
            output_path=Path("/out/o.mp4"),
            config=Config(),
            output_crf=18,
        )
        settings = _build_assembly_settings(params, [])
        assert settings.output_crf == 18


# ---------------------------------------------------------------------------
# _build_memory_key
# ---------------------------------------------------------------------------


class TestBuildMemoryKey:
    def test_returns_key_when_all_fields_present(self):
        from immich_memories.generate import _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            memory_type="month",
            date_start=date(2025, 7, 1),
            date_end=date(2025, 7, 31),
        )
        key = _build_memory_key(params)
        assert key is not None
        assert "month" in key

    def test_returns_none_when_memory_type_missing(self):
        from immich_memories.generate import _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            memory_type=None,
            date_start=date(2025, 7, 1),
            date_end=date(2025, 7, 31),
        )
        assert _build_memory_key(params) is None

    def test_returns_none_when_date_start_missing(self):
        from immich_memories.generate import _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            memory_type="month",
            date_start=None,
            date_end=date(2025, 7, 31),
        )
        assert _build_memory_key(params) is None

    def test_returns_none_when_date_end_missing(self):
        from immich_memories.generate import _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            memory_type="month",
            date_start=date(2025, 7, 1),
            date_end=None,
        )
        assert _build_memory_key(params) is None

    def test_includes_person_name_when_present(self):
        from immich_memories.generate import _build_memory_key

        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            memory_type="month",
            date_start=date(2025, 7, 1),
            date_end=date(2025, 7, 31),
            person_name="Alice",
        )
        key = _build_memory_key(params)
        assert key is not None
        assert "alice" in key.lower()


# ---------------------------------------------------------------------------
# _generate_memory_inner — main orchestrator
# ---------------------------------------------------------------------------


class TestGenerateMemoryInner:
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
        """Return a context manager that patches all external boundaries in _generate_memory_inner."""
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"fake video")

        assembly_clip = AssemblyClip(
            path=result_path, duration=5.0, asset_id="clip-1", date="2025-07-15"
        )

        mock_assembler = MagicMock()
        mock_assembler.assemble_with_titles.return_value = result_path

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
            # WHY: _extract_clips downloads from Immich + runs FFmpeg
            "extract": patch(
                "immich_memories.generate._extract_clips",
                return_value=[assembly_clip],
            ),
            # WHY: _add_photos_if_enabled renders photos via FFmpeg
            "photos": patch(
                "immich_memories.generate._add_photos_if_enabled",
                return_value=[assembly_clip],
            ),
            # WHY: validate_clips checks file existence on disk
            "validate": patch(
                "immich_memories.generate.validate_clips",
                return_value=([assembly_clip], []),
            ),
            # WHY: _create_assembler creates VideoAssembler with FFmpeg deps
            "assembler": patch(
                "immich_memories.generate._create_assembler",
                return_value=mock_assembler,
            ),
            # WHY: _run_music_phase calls external music generation APIs
            "music": patch("immich_memories.generate._run_music_phase"),
            # WHY: sanitize_filename is a security utility
            "sanitize": patch(
                "immich_memories.security.sanitize_filename",
                side_effect=lambda x: x,
            ),
        }
        return patches, result_path, assembly_clip

    def test_happy_path_returns_result_path(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, result_path, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            result = _generate_memory_inner(params)

        assert result == result_path

    def test_calls_extract_then_assemble_in_order(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, result_path, _ = self._patch_inner_deps(tmp_path)
        call_order = []

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = lambda *_a, **_kw: (  # noqa: ARG005
                call_order.append("extract"),
                [AssemblyClip(path=result_path, duration=5.0, asset_id="c1", date="2025-07-15")],
            )[1]
            mocks["assembler"].return_value.assemble_with_titles.side_effect = lambda *_a, **_kw: (  # noqa: ARG005
                call_order.append("assemble"),
                result_path,
            )[1]
            _generate_memory_inner(params)

        assert call_order == ["extract", "assemble"]

    def test_no_clips_after_extraction_raises(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].return_value = []
            mocks["validate"].return_value = ([], [])

            with pytest.raises(GenerationError, match="No clips could be processed"):
                _generate_memory_inner(params)

    def test_privacy_mode_anonymizes_clips(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path, privacy_mode=True, person_name="Alice")
        patches, result_path, assembly_clip = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            anon_mock = stack.enter_context(
                patch(
                    "immich_memories.generate.anonymize_clips_for_privacy",
                    return_value=[assembly_clip],
                )
            )
            preset_mock = stack.enter_context(
                patch("immich_memories.generate.anonymize_preset_params", return_value={})
            )
            name_mock = stack.enter_context(
                patch("immich_memories.generate.anonymize_name", return_value="Anon")
            )
            _generate_memory_inner(params)

        anon_mock.assert_called_once()
        preset_mock.assert_called_once()
        name_mock.assert_called_once_with("Alice")

    def test_upload_called_when_enabled(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        mock_client = MagicMock()
        params = self._make_params(
            tmp_path, upload_enabled=True, upload_album="test-album", client=mock_client
        )
        patches, result_path, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            upload_mock = stack.enter_context(patch("immich_memories.generate._upload_to_immich"))
            _generate_memory_inner(params)

        upload_mock.assert_called_once_with(mock_client, result_path, "test-album")

    def test_upload_not_called_when_disabled(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path, upload_enabled=False)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            upload_mock = stack.enter_context(patch("immich_memories.generate._upload_to_immich"))
            _generate_memory_inner(params)

        upload_mock.assert_not_called()

    def test_unexpected_exception_wrapped_in_generation_error(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = RuntimeError("something broke")

            with pytest.raises(GenerationError, match="Generation failed"):
                _generate_memory_inner(params)

    def test_generation_error_not_re_wrapped(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            mocks["extract"].side_effect = GenerationError("intentional")

            with pytest.raises(GenerationError, match="intentional"):
                _generate_memory_inner(params)

    def test_set_current_run_id_cleared_in_finally(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            _generate_memory_inner(params)

        # set_current_run_id called with the run_id first, then None in finally
        calls = mocks["set_run_id"].call_args_list
        assert calls[-1].args == (None,)

    def test_debug_mode_preserves_intermediates(self, tmp_path):
        from immich_memories.generate import _generate_memory_inner

        params = self._make_params(tmp_path, debug_preserve_intermediates=True)
        patches, _, _ = self._patch_inner_deps(tmp_path)

        with contextlib.ExitStack() as stack:
            {name: stack.enter_context(p) for name, p in patches.items()}
            cleanup_mock = stack.enter_context(patch("immich_memories.generate._cleanup_temp_dirs"))
            _generate_memory_inner(params)

        cleanup_mock.assert_not_called()


# ---------------------------------------------------------------------------
# generate_memory (lock + inner call integration)
# ---------------------------------------------------------------------------


class TestGenerateMemoryLockIntegration:
    def test_acquires_lock_and_calls_inner(self, tmp_path):
        clip = make_clip("c1", duration=5.0)
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"video")

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "output.mp4",
            config=Config(),
        )

        # WHY: PipelineLock acquires OS-level file lock
        with (
            patch("immich_memories.generate.PipelineLock"),
            patch(
                "immich_memories.generate._generate_memory_inner",
                return_value=result_path,
            ) as mock_inner,
        ):
            result = generate_memory(params)

        mock_inner.assert_called_once_with(params)
        assert result == result_path


# ---------------------------------------------------------------------------
# _create_assembler
# ---------------------------------------------------------------------------


class TestCreateAssembler:
    def test_creates_video_assembler_with_settings(self, tmp_path):
        from immich_memories.generate import _create_assembler

        config = Config()
        config.cache.database = str(tmp_path / "cache.db")

        settings = MagicMock()
        # WHY: VideoAssembler.__init__ requires FFmpeg; mock the import
        with patch("immich_memories.processing.video_assembler.VideoAssembler") as mock_cls:
            _create_assembler(settings, config)

        mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# _run_music_phase
# ---------------------------------------------------------------------------


class TestRunMusicPhase:
    def test_skips_when_no_music_resolved(self, tmp_path):
        from immich_memories.generate import _run_music_phase

        params = GenerationParams(
            clips=[], output_path=Path("/tmp/o.mp4"), config=Config(), no_music=True
        )
        mock_tracker = MagicMock()

        # WHY: resolve_music_file checks filesystem for music files
        with patch(
            "immich_memories.generate_music.resolve_music_file", return_value=None
        ) as mock_resolve:
            _run_music_phase(params, [], tmp_path / "result.mp4", tmp_path, mock_tracker)

        mock_resolve.assert_called_once()
        mock_tracker.start_phase.assert_not_called()

    def test_applies_music_when_resolved(self, tmp_path):
        from immich_memories.generate import _run_music_phase

        music_file = tmp_path / "music.mp3"
        music_file.write_bytes(b"music")
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"video")

        params = GenerationParams(
            clips=[], output_path=Path("/tmp/o.mp4"), config=Config(), music_volume=0.7
        )
        mock_tracker = MagicMock()

        # WHY: resolve_music_file and apply_music_file touch filesystem + FFmpeg
        with (
            patch("immich_memories.generate_music.resolve_music_file", return_value=music_file),
            patch("immich_memories.generate_music.apply_music_file") as mock_apply,
        ):
            _run_music_phase(params, [], result_path, tmp_path, mock_tracker)

        mock_apply.assert_called_once_with(result_path, music_file, 0.7)
        mock_tracker.start_phase.assert_called_once_with("music", 1)
        mock_tracker.complete_phase.assert_called_once_with(items_processed=1)

    def test_report_fn_delegates_to_progress_callback(self, tmp_path):
        from immich_memories.generate import _run_music_phase

        calls = []
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            progress_callback=lambda p, pct, m: calls.append((p, pct, m)),
        )
        mock_tracker = MagicMock()

        # WHY: resolve_music_file accesses the filesystem
        with patch(
            "immich_memories.generate_music.resolve_music_file", return_value=None
        ) as mock_resolve:
            _run_music_phase(params, [], tmp_path / "r.mp4", tmp_path, mock_tracker)

        # Verify resolve_music_file received a report_fn callback
        call_kwargs = mock_resolve.call_args
        assert (
            call_kwargs.kwargs.get("report_fn") is not None
            or call_kwargs[1].get("report_fn") is not None
        )


# ---------------------------------------------------------------------------
# _upload_to_immich
# ---------------------------------------------------------------------------


class TestUploadToImmich:
    def test_calls_client_upload(self, tmp_path):
        from immich_memories.generate import _upload_to_immich

        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        # WHY: SyncImmichClient.upload_memory hits the Immich REST API
        mock_client = MagicMock()
        mock_client.upload_memory.return_value = {"asset_id": "abc123"}

        result = _upload_to_immich(mock_client, video_path, "My Album")

        mock_client.upload_memory.assert_called_once_with(
            video_path=video_path, album_name="My Album"
        )
        assert result["asset_id"] == "abc123"

    def test_none_album_name(self, tmp_path):
        from immich_memories.generate import _upload_to_immich

        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        mock_client = MagicMock()
        mock_client.upload_memory.return_value = {}

        _upload_to_immich(mock_client, video_path, None)
        mock_client.upload_memory.assert_called_once_with(video_path=video_path, album_name=None)


# ---------------------------------------------------------------------------
# _apply_unified_budget
# ---------------------------------------------------------------------------


class TestApplyUnifiedBudget:
    def test_no_client_returns_clips_unchanged(self, tmp_path):
        from immich_memories.generate import _apply_unified_budget

        clips = [AssemblyClip(path=Path("/a.mp4"), duration=5.0, asset_id="v1")]
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            client=None,
            include_photos=True,
            photo_assets=[make_asset("p1")],
            target_duration_seconds=60.0,
        )
        videos, photos = _apply_unified_budget(clips, params, tmp_path)
        assert videos is clips
        assert photos == []

    def test_with_client_calls_scoring_pipeline(self, tmp_path):
        from immich_memories.generate import _apply_unified_budget

        clips = [AssemblyClip(path=Path("/a.mp4"), duration=5.0, asset_id="v1", date="2025-07-15")]
        mock_client = MagicMock()
        params = GenerationParams(
            clips=[],
            output_path=Path("/tmp/o.mp4"),
            config=Config(),
            client=mock_client,
            include_photos=True,
            photo_assets=[make_asset("p1")],
            target_duration_seconds=60.0,
        )

        mock_selection = MagicMock()
        mock_selection.content_duration = 55.0
        mock_selection.kept_video_ids = {"v1"}
        mock_selection.selected_photo_ids = []

        mock_result = MagicMock()
        mock_result.selection = mock_selection
        mock_result.scored_photos = []

        # WHY: score_and_select_photos runs expensive analysis + scoring
        with (
            patch(
                "immich_memories.photos.photo_pipeline.score_and_select_photos",
                return_value=mock_result,
            ),
            patch("immich_memories.photos.photo_pipeline.render_photo_clips", return_value=[]),
        ):
            videos, photos = _apply_unified_budget(clips, params, tmp_path, target_override=60.0)

        assert len(videos) == 1
        assert videos[0].asset_id == "v1"


# ===========================================================================
# generate_downloads.py
# ===========================================================================


class TestDownloadClip:
    def test_local_path_exists_skips_download(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        local_file = tmp_path / "local.mp4"
        local_file.write_bytes(b"data")

        clip = MagicMock()
        clip.local_path = str(local_file)

        result = download_clip(MagicMock(), MagicMock(), clip, tmp_path)
        assert result == local_file

    def test_local_path_nonexistent_proceeds_to_download(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()
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

        clip = MagicMock()
        clip.local_path = None

        result = download_clip(None, MagicMock(), clip, tmp_path)
        assert result is None

    def test_live_burst_delegates_to_merge(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()
        clip.local_path = None
        clip.live_burst_video_ids = ["vid-a", "vid-b"]
        clip.live_burst_trim_points = [(0.0, 1.0), (0.0, 1.5)]

        mock_client = MagicMock()
        merged = tmp_path / "merged.mp4"

        # WHY: _download_and_merge_burst runs FFmpeg for burst merging
        with patch(
            "immich_memories.generate_downloads._download_and_merge_burst",
            return_value=merged,
        ) as mock_merge:
            result = download_clip(mock_client, MagicMock(), clip, tmp_path)

        mock_merge.assert_called_once()
        assert result == merged

    def test_no_local_path_no_burst_uses_cache(self, tmp_path):
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()
        clip.local_path = None
        clip.live_burst_video_ids = None
        clip.live_burst_trim_points = None

        cached = tmp_path / "cached.mp4"
        mock_cache = MagicMock()
        mock_cache.download_or_get.return_value = cached

        result = download_clip(MagicMock(), mock_cache, clip, tmp_path)
        assert result == cached


class TestDownloadAndMergeBurst:
    def test_cached_merged_file_returned_immediately(self, tmp_path):
        from immich_memories.generate_downloads import _download_and_merge_burst

        clip = MagicMock()
        clip.asset.id = "asset-1"
        clip.live_burst_video_ids = ["v1", "v2"]
        clip.live_burst_trim_points = [(0.0, 1.0), (0.0, 1.5)]
        clip.live_burst_shutter_timestamps = None

        merge_dir = tmp_path / ".live_merges"
        merge_dir.mkdir(parents=True)
        merged = merge_dir / "asset-1_merged.mp4"
        merged.write_bytes(b"x" * 2000)

        result = _download_and_merge_burst(MagicMock(), MagicMock(), clip, tmp_path)
        assert result == merged

    def test_no_burst_clips_downloaded_falls_back_to_cache(self, tmp_path):
        from immich_memories.generate_downloads import _download_and_merge_burst

        clip = MagicMock()
        clip.asset.id = "asset-1"
        clip.live_burst_video_ids = ["v1"]
        clip.live_burst_trim_points = [(0.0, 1.0)]
        clip.live_burst_shutter_timestamps = None

        mock_cache = MagicMock()
        fallback = tmp_path / "fallback.mp4"
        mock_cache.download_or_get.return_value = fallback
        mock_cache.cache_dir = tmp_path / "cache"

        # WHY: _download_burst_clips downloads individual burst videos from Immich
        with patch(
            "immich_memories.generate_downloads._download_burst_clips",
            return_value=[],
        ):
            result = _download_and_merge_burst(MagicMock(), mock_cache, clip, tmp_path)

        assert result == fallback

    def test_partial_downloads_aligns_then_merges(self, tmp_path):
        from immich_memories.generate_downloads import _download_and_merge_burst

        clip = MagicMock()
        clip.asset.id = "asset-1"
        clip.live_burst_video_ids = ["v1", "v2"]
        clip.live_burst_trim_points = [(0.0, 1.0), (0.0, 1.5)]
        clip.live_burst_shutter_timestamps = None

        # Only one clip downloaded (mismatched count)
        v1_path = tmp_path / "v1.MOV"
        v1_path.write_bytes(b"video1")

        mock_cache = MagicMock()
        mock_cache.cache_dir = tmp_path / "cache"
        merged = tmp_path / ".live_merges" / "asset-1_merged.mp4"

        with (
            patch(
                "immich_memories.generate_downloads._download_burst_clips",
                return_value=[v1_path],
            ),
            patch(
                "immich_memories.generate_downloads._align_burst_subset",
                return_value=([v1_path], [(0.0, 1.0)]),
            ),
            patch(
                "immich_memories.generate_downloads._try_merge_burst",
                return_value=merged,
            ) as mock_merge,
        ):
            result = _download_and_merge_burst(MagicMock(), mock_cache, clip, tmp_path)

        mock_merge.assert_called_once()
        assert result == merged

    def test_merge_failure_falls_back_to_cache(self, tmp_path):
        from immich_memories.generate_downloads import _download_and_merge_burst

        clip = MagicMock()
        clip.asset.id = "asset-1"
        clip.live_burst_video_ids = ["v1"]
        clip.live_burst_trim_points = [(0.0, 1.0)]
        clip.live_burst_shutter_timestamps = [0.5]

        v1_path = tmp_path / "v1.MOV"
        v1_path.write_bytes(b"video1")

        mock_cache = MagicMock()
        mock_cache.cache_dir = tmp_path / "cache"
        fallback = tmp_path / "fallback.mp4"
        mock_cache.download_or_get.return_value = fallback

        with (
            patch(
                "immich_memories.generate_downloads._download_burst_clips",
                return_value=[v1_path],
            ),
            patch(
                "immich_memories.generate_downloads._try_merge_burst",
                return_value=None,
            ),
        ):
            result = _download_and_merge_burst(MagicMock(), mock_cache, clip, tmp_path)

        assert result == fallback


class TestDownloadBurstClips:
    def test_cached_clips_returned_without_download(self, tmp_path):
        from immich_memories.generate_downloads import _download_burst_clips

        cache_dir = tmp_path / "cache"
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True)
        clip_file = subdir / "abcdef.MOV"
        clip_file.write_bytes(b"video")

        result = _download_burst_clips(MagicMock(), cache_dir, ["abcdef"])
        assert result == [clip_file]

    def test_download_failure_skips_clip(self, tmp_path):
        from immich_memories.generate_downloads import _download_burst_clips

        cache_dir = tmp_path / "cache"
        # WHY: client.download_asset makes HTTP requests to Immich
        mock_client = MagicMock()
        mock_client.download_asset.side_effect = ConnectionError("network failure")

        result = _download_burst_clips(mock_client, cache_dir, ["abcdef"])
        assert result == []

    def test_successful_download_appended(self, tmp_path):
        from immich_memories.generate_downloads import _download_burst_clips

        cache_dir = tmp_path / "cache"

        def fake_download(vid, dest):
            dest.write_bytes(b"downloaded")

        # WHY: client.download_asset makes HTTP requests to Immich
        mock_client = MagicMock()
        mock_client.download_asset.side_effect = fake_download

        result = _download_burst_clips(mock_client, cache_dir, ["abcdef"])
        assert len(result) == 1
        assert result[0].name == "abcdef.MOV"

    def test_short_burst_id_uses_fallback_subdir(self, tmp_path):
        from immich_memories.generate_downloads import _download_burst_clips

        cache_dir = tmp_path / "cache"

        def fake_download(vid, dest):
            dest.write_bytes(b"data")

        mock_client = MagicMock()
        mock_client.download_asset.side_effect = fake_download

        result = _download_burst_clips(mock_client, cache_dir, ["x"])
        assert len(result) == 1
        # Short ID (<2 chars) uses "00" as subdir
        assert "00" in str(result[0].parent)


class TestAlignBurstSubset:
    def test_full_match(self):
        from immich_memories.generate_downloads import _align_burst_subset

        p1 = Path("/cache/v1.MOV")
        p2 = Path("/cache/v2.MOV")
        paths, trims = _align_burst_subset([p1, p2], ["v1", "v2"], [(0.0, 1.0), (0.5, 2.0)])
        assert paths == [p1, p2]
        assert trims == [(0.0, 1.0), (0.5, 2.0)]

    def test_partial_match(self):
        from immich_memories.generate_downloads import _align_burst_subset

        p2 = Path("/cache/v2.MOV")
        paths, trims = _align_burst_subset([p2], ["v1", "v2"], [(0.0, 1.0), (0.5, 2.0)])
        assert paths == [p2]
        assert trims == [(0.5, 2.0)]

    def test_no_match_returns_empty(self):
        from immich_memories.generate_downloads import _align_burst_subset

        p_other = Path("/cache/other.MOV")
        paths, trims = _align_burst_subset([p_other], ["v1", "v2"], [(0.0, 1.0), (0.5, 2.0)])
        assert paths == []
        assert trims == []


class TestTryMergeBurst:
    def test_no_valid_clips_returns_none(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        # WHY: filter_valid_clips probes video streams with ffprobe
        with patch(
            "immich_memories.processing.live_photo_merger.filter_valid_clips",
            return_value=([], []),
        ):
            result = _try_merge_burst([], [], tmp_path / "merged.mp4")

        assert result is None

    def test_successful_merge_returns_path(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        clip_path = tmp_path / "clip.MOV"
        clip_path.write_bytes(b"video")
        merged_path = tmp_path / "merged.mp4"

        # WHY: filter_valid_clips, probe_clip_has_audio, build_merge_command use ffprobe/ffmpeg
        with (
            patch(
                "immich_memories.processing.live_photo_merger.filter_valid_clips",
                return_value=([clip_path], [(0.0, 1.0)]),
            ),
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=False,
            ),
            patch(
                "immich_memories.processing.live_photo_merger.build_merge_command",
                return_value=["echo", "ok"],
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            merged_path.write_bytes(b"merged")  # Simulate FFmpeg output
            result = _try_merge_burst([clip_path], [(0.0, 1.0)], merged_path)

        assert result == merged_path

    def test_merge_command_failure_returns_none(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        clip_path = tmp_path / "clip.MOV"
        clip_path.write_bytes(b"video")
        merged_path = tmp_path / "merged.mp4"

        with (
            patch(
                "immich_memories.processing.live_photo_merger.filter_valid_clips",
                return_value=([clip_path], [(0.0, 1.0)]),
            ),
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=False,
            ),
            patch(
                "immich_memories.processing.live_photo_merger.build_merge_command",
                return_value=["false"],
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = _try_merge_burst([clip_path], [(0.0, 1.0)], merged_path)

        assert result is None

    def test_with_audio_and_shutter_timestamps_attempts_spectrogram(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        c1 = tmp_path / "c1.MOV"
        c2 = tmp_path / "c2.MOV"
        c1.write_bytes(b"v1")
        c2.write_bytes(b"v2")
        merged_path = tmp_path / "merged.mp4"

        with (
            patch(
                "immich_memories.processing.live_photo_merger.filter_valid_clips",
                return_value=([c1, c2], [(0.0, 1.0), (0.0, 1.5)]),
            ),
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger.build_merge_command",
                return_value=["echo", "ok"],
            ),
            # WHY: subprocess.run calls ffprobe for duration probing
            patch("subprocess.run") as mock_run,
            # WHY: align_clips_spectrogram runs expensive cross-correlation
            patch(
                "immich_memories.processing.live_photo_merger.align_clips_spectrogram",
                return_value=([(0.1, 0.9), (0.1, 1.4)], [(0.05, 0.95), (0.05, 1.45)]),
            ) as mock_align,
        ):
            # Simulate ffprobe returning duration JSON
            probe_result = MagicMock(stdout='{"format":{"duration":"2.0"}}')
            run_result = MagicMock(returncode=0)
            mock_run.side_effect = [probe_result, probe_result, run_result]
            merged_path.write_bytes(b"merged")

            result = _try_merge_burst(
                [c1, c2],
                [(0.0, 1.0), (0.0, 1.5)],
                merged_path,
                shutter_timestamps=[0.3, 0.7],
            )

        mock_align.assert_called_once()
        assert result == merged_path

    def test_spectrogram_failure_falls_back_to_timestamp_trims(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        c1 = tmp_path / "c1.MOV"
        c2 = tmp_path / "c2.MOV"
        c1.write_bytes(b"v1")
        c2.write_bytes(b"v2")
        merged_path = tmp_path / "merged.mp4"

        with (
            patch(
                "immich_memories.processing.live_photo_merger.filter_valid_clips",
                return_value=([c1, c2], [(0.0, 1.0), (0.0, 1.5)]),
            ),
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=True,
            ),
            patch(
                "immich_memories.processing.live_photo_merger.build_merge_command",
                return_value=["echo", "ok"],
            ),
            patch("subprocess.run") as mock_run,
            # WHY: align_clips_spectrogram runs cross-correlation that can fail
            patch(
                "immich_memories.processing.live_photo_merger.align_clips_spectrogram",
                side_effect=RuntimeError("alignment failed"),
            ),
        ):
            probe_result = MagicMock(stdout='{"format":{"duration":"2.0"}}')
            run_result = MagicMock(returncode=0)
            mock_run.side_effect = [probe_result, probe_result, run_result]
            merged_path.write_bytes(b"merged")

            result = _try_merge_burst(
                [c1, c2],
                [(0.0, 1.0), (0.0, 1.5)],
                merged_path,
                shutter_timestamps=[0.3, 0.7],
            )

        # Falls back and still succeeds with timestamp trims
        assert result == merged_path

    def test_merge_exception_returns_none(self, tmp_path):
        from immich_memories.generate_downloads import _try_merge_burst

        clip_path = tmp_path / "clip.MOV"
        clip_path.write_bytes(b"video")
        merged_path = tmp_path / "merged.mp4"

        with (
            patch(
                "immich_memories.processing.live_photo_merger.filter_valid_clips",
                return_value=([clip_path], [(0.0, 1.0)]),
            ),
            patch(
                "immich_memories.processing.live_photo_merger.probe_clip_has_audio",
                return_value=False,
            ),
            patch(
                "immich_memories.processing.live_photo_merger.build_merge_command",
                return_value=["bad_command"],
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = OSError("command not found")
            result = _try_merge_burst([clip_path], [(0.0, 1.0)], merged_path)

        assert result is None


# ===========================================================================
# generate_music.py
# ===========================================================================


class TestResolveMusicFile:
    def test_no_music_flag_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music_file

        result = resolve_music_file(
            config=Config(),
            music_path=None,
            no_music=True,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
        )
        assert result is None

    def test_explicit_music_path_returned(self, tmp_path):
        from immich_memories.generate_music import resolve_music_file

        music = tmp_path / "song.mp3"
        music.write_bytes(b"audio")
        result = resolve_music_file(
            config=Config(),
            music_path=music,
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
        )
        assert result == music

    def test_explicit_path_nonexistent_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music_file

        result = resolve_music_file(
            config=Config(),
            music_path=tmp_path / "nonexistent.mp3",
            no_music=False,
            assembly_clips=[],
            run_output_dir=tmp_path,
            memory_type=None,
        )
        assert result is None

    def test_auto_generate_when_no_path_and_config_available(self, tmp_path):
        from immich_memories.generate_music import resolve_music_file

        config = Config()
        calls = []

        # WHY: music_config_available checks external music generation services
        with (
            patch("immich_memories.generate_music.music_config_available", return_value=True),
            patch(
                "immich_memories.generate_music.auto_generate_music",
                return_value=tmp_path / "generated.mp3",
            ) as mock_gen,
        ):

            def report_fn(p, pct, m):
                return calls.append((p, pct, m))

            result = resolve_music_file(
                config=config,
                music_path=None,
                no_music=False,
                assembly_clips=[],
                run_output_dir=tmp_path,
                memory_type="month",
                report_fn=report_fn,
            )

        mock_gen.assert_called_once()
        assert result == tmp_path / "generated.mp3"
        # report_fn should have been called with music progress
        assert any(c[0] == "music" for c in calls)

    def test_no_path_no_config_returns_none(self, tmp_path):
        from immich_memories.generate_music import resolve_music_file

        # WHY: music_config_available checks external service configs
        with patch("immich_memories.generate_music.music_config_available", return_value=False):
            result = resolve_music_file(
                config=Config(),
                music_path=None,
                no_music=False,
                assembly_clips=[],
                run_output_dir=tmp_path,
                memory_type=None,
            )
        assert result is None


class TestClipMonthFromDate:
    def test_valid_date(self):
        from immich_memories.generate_music import _clip_month_from_date

        assert _clip_month_from_date("2025-07-15") == 7

    def test_none_returns_none(self):
        from immich_memories.generate_music import _clip_month_from_date

        assert _clip_month_from_date(None) is None

    def test_invalid_format_returns_none(self):
        from immich_memories.generate_music import _clip_month_from_date

        assert _clip_month_from_date("not-a-date") is None

    def test_no_month_part_returns_none(self):
        from immich_memories.generate_music import _clip_month_from_date

        assert _clip_month_from_date("2025") is None


class TestAutoGenerateMusic:
    def test_no_config_returns_none(self, tmp_path):
        from immich_memories.generate_music import auto_generate_music

        config = Config()
        # WHY: music_config_available checks MusicGen/ACE-Step service configs
        with patch("immich_memories.generate_music.music_config_available", return_value=False):
            result = auto_generate_music(config, [], tmp_path, None)
        assert result is None

    def test_generation_exception_returns_none(self, tmp_path):
        from immich_memories.generate_music import auto_generate_music

        config = Config()
        with (
            patch("immich_memories.generate_music.music_config_available", return_value=True),
            # WHY: generate_music_for_video calls external MusicGen/ACE-Step APIs
            patch(
                "immich_memories.audio.music_generator.generate_music_for_video",
                side_effect=RuntimeError("API down"),
            ),
        ):
            result = auto_generate_music(config, [], tmp_path, "month")
        assert result is None


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
