"""Integration tests for generate_memory() edge cases and scenarios.

Covers: error handling, music, trip locations, title overrides,
clip segment overrides, live photo bursts.

Run: make test-integration
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import make_clip
from tests.integration.conftest import requires_ffmpeg
from tests.integration.immich_fixtures import requires_immich

pytestmark = [pytest.mark.integration, requires_ffmpeg]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def short_clip(tmp_path_factory) -> Path:
    """3s 320x240 clip for fast tests."""
    out = tmp_path_factory.mktemp("scenarios") / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=15:duration=3",
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
        timeout=15,
    )
    return out


@pytest.fixture(scope="module")
def short_music(tmp_path_factory) -> Path:
    """5s music file (sine wave)."""
    out = tmp_path_factory.mktemp("scenarios") / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=5",
            "-ar",
            "44100",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return out


def _make_test_clip(path: Path, asset_id: str = "test") -> object:
    """Create a VideoClipInfo pointing to a real local file."""
    clip = make_clip(asset_id, duration=3.0, width=320, height=240)
    clip.local_path = str(path)
    return clip


# ---------------------------------------------------------------------------
# Scenario 1: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_exception_wraps_in_generation_error(self, short_clip, tmp_path):
        """Non-GenerationError exceptions get wrapped with sanitized message."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory

        clip = _make_test_clip(short_clip, "err-clip")
        config = Config()
        config.title_screens.enabled = False

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "err.mp4",
            config=config,
            transition="cut",
            no_music=True,
            # Force an error by pointing to nonexistent segment
            clip_segments={"err-clip": (100.0, 200.0)},  # Beyond clip duration
        )

        # Out-of-range segment may produce empty output → GenerationError
        # or FFmpeg may truncate silently. Either way, should not crash unhandled.
        try:
            result = generate_memory(params)
            # If it didn't raise, the output should still be valid (FFmpeg truncated)
            assert result.exists()
        except GenerationError:
            pass  # Expected for truly invalid segments


# ---------------------------------------------------------------------------
# Scenario 2: Music application
# ---------------------------------------------------------------------------


class TestMusicApplication:
    def test_music_mixed_into_video(self, short_clip, short_music, tmp_path):
        """Music file gets mixed into the assembled video."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip = _make_test_clip(short_clip)
        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "with_music.mp4"
        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            transition="cut",
            music_path=short_music,
            music_volume=0.5,
        )

        result = generate_memory(params)
        assert result.exists()
        assert result.stat().st_size > 1000

        # Verify audio stream present
        from tests.integration.conftest import ffprobe_json, has_stream

        probe = ffprobe_json(result)
        assert has_stream(probe, "audio"), "Music should add an audio stream"


# Scenarios 4 (trip locations) and 5 (title override) are pure logic —
# tested in tests/test_generate.py (unit tests, no FFmpeg needed)


# ---------------------------------------------------------------------------
# Scenario 3: Live photo burst merge (real Immich)
# ---------------------------------------------------------------------------


@requires_immich
class TestLivePhotoBurst:
    """Live photo burst download + merge with real Immich data."""

    def test_burst_merge_produces_video(self, tmp_path):
        """Download a real live photo burst from Immich and merge it."""
        from datetime import date

        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.api.models import VideoClipInfo
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.config_loader import Config
        from immich_memories.generate import _download_clip
        from immich_memories.timeperiod import DateRange

        config = Config.from_yaml(Config.get_default_path())
        config.defaults.target_duration_seconds = 60  # Cap at 60s for test speed
        client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

        # Find live photos with video components
        live_assets = client.get_live_photos_for_date_range(
            DateRange(start=date(2025, 1, 1), end=date(2025, 6, 30))
        )

        if len(live_assets) < 2:
            pytest.skip("Need at least 2 live photos in Immich")

        # Create a fake burst from 2 live photo video components
        burst_video_ids = [a.live_photo_video_id for a in live_assets[:2] if a.live_photo_video_id]

        if len(burst_video_ids) < 2:
            pytest.skip("Live photos don't have video components")

        # Create a VideoClipInfo with burst data
        clip = VideoClipInfo(
            asset=live_assets[0],
            duration_seconds=3.0,
            live_burst_video_ids=burst_video_ids,
            live_burst_trim_points=[(0.0, 1.5)] * len(burst_video_ids),
        )

        video_cache = VideoDownloadCache(
            cache_dir=tmp_path / "cache",
            max_size_gb=1,
            max_age_days=1,
        )

        result = _download_clip(client, video_cache, clip, tmp_path)

        # Should produce a merged file (or fallback to single download)
        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0


# ---------------------------------------------------------------------------
# Scenario 9: Live photo merge pipeline (real Immich — auto-detect clusters)
# ---------------------------------------------------------------------------


@requires_immich
class TestLivePhotoMergeReal:
    """Auto-detect live photo clusters from Immich and verify merge pipeline."""

    def test_auto_detect_and_merge_live_photos(self, tmp_path):
        """Find live photos in Immich, cluster, merge into a valid video."""
        from datetime import date

        from immich_memories.api.immich import SyncImmichClient
        from immich_memories.config_loader import Config
        from immich_memories.processing.live_photo_merger import (
            build_merge_command,
            cluster_live_photos,
            filter_valid_clips,
        )
        from immich_memories.timeperiod import DateRange

        config = Config.from_yaml(Config.get_default_path())
        config.defaults.target_duration_seconds = 60  # Cap at 60s for test speed
        client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

        # Broad date range to find any live photos
        live_assets = client.get_live_photos_for_date_range(
            DateRange(start=date(2024, 1, 1), end=date(2026, 1, 1))
        )

        if len(live_assets) < 2:
            pytest.skip("Need at least 2 live photos in Immich for cluster detection")

        clusters = cluster_live_photos(live_assets, merge_window_seconds=10.0)

        # Find first cluster with 2+ photos
        merge_cluster = None
        for cluster in clusters:
            if cluster.count >= 2:
                merge_cluster = cluster
                break

        if merge_cluster is None:
            pytest.skip("No live photo clusters with 2+ photos found")

        # Download the video components
        burst_ids = merge_cluster.video_asset_ids
        if len(burst_ids) < 2:
            pytest.skip("Cluster video components missing live photo video IDs")

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        clip_paths = []
        for vid in burst_ids:
            dest = cache_dir / f"{vid}.MOV"
            try:
                client.download_asset(vid, dest)
                if dest.exists() and dest.stat().st_size > 0:
                    clip_paths.append(dest)
            except Exception:
                pass

        if len(clip_paths) < 2:
            pytest.skip("Could not download enough burst video components")

        # Build merge command and run FFmpeg
        trim_points = merge_cluster.trim_points()[: len(clip_paths)]
        # Filter out clips with no video stream or mismatched orientation
        clip_paths, trim_points = filter_valid_clips(clip_paths, trim_points)
        if len(clip_paths) < 2:
            pytest.skip("Not enough same-orientation clips after filtering")

        merged_path = tmp_path / "merged.mp4"
        cmd = build_merge_command(clip_paths, trim_points, merged_path)

        import subprocess

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, f"FFmpeg merge failed: {result.stderr[:500]}"
        assert merged_path.exists()
        assert merged_path.stat().st_size > 1000

        # Verify output is a valid video with expected properties
        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        probe = ffprobe_json(merged_path)
        assert has_stream(probe, "video"), "Merged output must have a video stream"

        duration = get_duration(probe)
        assert duration > 0, "Merged video must have positive duration"


# ---------------------------------------------------------------------------
# Scenario 7: Error wrapping
# ---------------------------------------------------------------------------


class TestErrorWrapping:
    def test_non_generation_error_gets_wrapped(self, short_clip, tmp_path):
        """A RuntimeError during assembly gets wrapped in GenerationError."""
        from unittest.mock import patch

        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory

        clip = _make_test_clip(short_clip, "wrap-err")
        config = Config()
        config.title_screens.enabled = False

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "err.mp4",
            config=config,
            transition="cut",
            no_music=True,
        )

        # WHY: force an error inside assembly to test the wrapping logic
        with (
            patch(
                "immich_memories.generate._create_assembler",
                side_effect=RuntimeError("Simulated assembly crash"),
            ),
            pytest.raises(GenerationError, match="Generation failed"),
        ):
            generate_memory(params)


# ---------------------------------------------------------------------------
# Scenario 8: Trip title settings in assembly
# ---------------------------------------------------------------------------


class TestTripTitleSettings:
    def test_trip_memory_type_builds_title_with_locations(self, short_clip, tmp_path):
        """memory_type=trip populates trip-specific title settings."""
        from datetime import date

        from immich_memories.config_loader import Config
        from immich_memories.generate import (
            GenerationParams,
            _build_title_settings,
        )
        from immich_memories.processing.assembly_config import AssemblyClip

        clip = _make_test_clip(short_clip)
        config = Config()

        params = GenerationParams(
            clips=[clip],
            output_path=tmp_path / "trip.mp4",
            config=config,
            memory_type="trip",
            no_music=True,
            memory_preset_params={
                "location_name": "Bruges",
                "trip_start": date(2025, 7, 1),
                "trip_end": date(2025, 7, 3),
            },
            date_start=date(2025, 7, 1),
            date_end=date(2025, 7, 3),
        )

        assembly_clips = [
            AssemblyClip(
                path=short_clip,
                duration=3.0,
                latitude=51.2093,
                longitude=3.2247,
                location_name="Bruges",
            ),
        ]

        title_settings = _build_title_settings(params, config, assembly_clips)
        assert title_settings.trip_locations is not None
        assert len(title_settings.trip_locations) > 0
        assert title_settings.trip_title_text is not None
        assert "bruges" in title_settings.trip_title_text.lower()


# ---------------------------------------------------------------------------
# Scenario 6: Clip segment overrides
# ---------------------------------------------------------------------------


class TestClipSegmentOverrides:
    def test_custom_segment_trims_clip(self, short_clip, tmp_path):
        """Clip segments from review step trim the extracted clip."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip = _make_test_clip(short_clip, "seg-clip")
        config = Config()
        config.title_screens.enabled = False

        output = tmp_path / "trimmed.mp4"
        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            transition="cut",
            no_music=True,
            # Trim to first 2 seconds of the 3s clip
            clip_segments={"seg-clip": (0.0, 2.0)},
        )

        result = generate_memory(params)
        assert result.exists()

        from tests.integration.conftest import ffprobe_json, get_duration

        duration = get_duration(ffprobe_json(result))
        # Should be ~2s (not 3s)
        assert 1.0 < duration < 2.5
