"""Additional behavior tests for generate.py covering uncovered branches."""

from __future__ import annotations

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
        with patch("immich_memories.generate._apply_unified_budget") as mock_budget:
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
        with patch("immich_memories.generate._apply_unified_budget") as mock_budget:
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
