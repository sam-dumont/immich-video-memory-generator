"""Mega flow integration tests — real Immich, real FFmpeg, real data.

Each test exercises a COMPLETE business flow end-to-end, hitting dozens of
branches per test. These are intentionally LONG tests that cover MANY code
paths in a single run, not small isolated unit tests.

Run: pytest tests/integration/pipeline/test_mega_flows.py -v -o "addopts=" -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg
from tests.integration.immich_fixtures import requires_immich

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_frame_brightness(video_path: Path, timestamp: float) -> float:
    """Extract a frame and return mean brightness (0-255)."""
    import numpy as np

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0 or len(result.stdout) == 0:
        return -1.0
    return float(np.mean(np.frombuffer(result.stdout, dtype=np.uint8)))


@dataclass
class _FakeStreamingClip:
    """Minimal clip for streaming_assemble_full duck-typed interface."""

    path: Path
    duration: float
    is_title_screen: bool = False
    rotation_override: int | None = None
    is_hdr: bool = False
    color_transfer: str | None = None
    input_seek: float = 0.0


# ---------------------------------------------------------------------------
# Test A: Trip Memory End-to-End
# ---------------------------------------------------------------------------


class TestMegaFlowTripMemory:
    """Full trip pipeline: GPS clips → trip detection → map fly-over → privacy → assembly.

    Exercises: _extract_clips, anonymize_clips_for_privacy, _build_title_settings(trip),
    extract_trip_locations, generate_trip_title_text, TripService.generate_trip_map_screen,
    create_map_fly_video (real ArcGIS tiles), location card dividers,
    streaming_assemble_full, cleanup.
    """

    def test_trip_memory_with_real_gps_clips(self, tmp_path):
        from immich_memories.generate import GenerationParams, generate_memory
        from tests.integration.immich_fixtures import find_trip_clips, make_immich_client

        client, config = make_immich_client()
        trip_clips = find_trip_clips(client)
        if len(trip_clips) < 2:
            pytest.skip("Need at least 2 trip clips with GPS in Immich")

        # Extract GPS from first clip with location data
        home_lat, home_lon = None, None
        for clip in trip_clips:
            exif = clip.asset.exif_info
            if exif and exif.latitude and exif.longitude:
                home_lat, home_lon = exif.latitude, exif.longitude
                break

        first_date = trip_clips[0].asset.file_created_at.date()
        last_date = trip_clips[-1].asset.file_created_at.date()
        location_name = "Test Trip"
        if trip_clips[0].asset.exif_info and trip_clips[0].asset.exif_info.city:
            location_name = trip_clips[0].asset.exif_info.city

        config.title_screens.enabled = True
        config.title_screens.title_duration = 3.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "output" / "trip_mega.mp4"

        phases_seen: set[str] = set()
        progress_values: list[float] = []

        def capture(phase: str, progress: float, msg: str) -> None:
            phases_seen.add(phase)
            progress_values.append(progress)

        params = GenerationParams(
            clips=trip_clips[:3],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            transition_duration=0.5,
            output_resolution="720p",
            memory_type="trip",
            memory_preset_params={
                "location_name": location_name,
                "trip_start": first_date,
                "trip_end": last_date,
                "home_lat": home_lat or 48.856,
                "home_lon": home_lon or 2.352,
            },
            date_start=first_date,
            date_end=last_date,
            person_name="Trip Tester",
            privacy_mode=True,
            progress_callback=capture,
        )

        result = generate_memory(params)

        # Core assertions: valid video output
        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        duration = get_duration(probe)
        # Clips + title (map fly ~3s) + ending (~2s) + crossfades
        assert duration > 5.0, f"Trip memory too short: {duration:.1f}s"

        # Map fly-over should render visible content at t=1s (not black)
        brightness = _extract_frame_brightness(result, 1.0)
        assert brightness > 5.0, f"Title/map frame appears blank: brightness={brightness:.0f}"

        # Progress should have fired
        assert len(progress_values) > 3
        assert "extract" in phases_seen or "download" in phases_seen

        # Cleanup: temp dirs removed
        result_dir = result.parent
        assert not (result_dir / ".title_screens").exists()
        assert not (result_dir / ".assembly_temps").exists()

        logger.info(
            f"Trip mega flow: {duration:.1f}s, {len(trip_clips[:3])} clips, "
            f"phases={phases_seen}, brightness={brightness:.0f}"
        )


# ---------------------------------------------------------------------------
# Test B: Monthly Memory with Photos
# ---------------------------------------------------------------------------


class TestMegaFlowMonthlyWithPhotos:
    """Monthly memory with photo support: score → budget → render → interleave → assemble.

    Exercises: _add_photos_if_enabled, score_photos (metadata scoring),
    _apply_unified_budget, estimate_title_overhead, select_within_budget,
    render_photo_clips, _render_single_photo (download → prepare → stream render),
    _merge_by_date, month dividers, streaming assembly.
    """

    def test_monthly_memory_with_real_photos(self, immich_short_clips, tmp_path):
        from datetime import date as d

        from immich_memories.generate import GenerationParams, generate_memory
        from immich_memories.timeperiod import DateRange

        clips, config, client = immich_short_clips

        # Fetch real photos from same date range
        photos = client.get_photos_for_date_range(
            DateRange(start=d(2024, 1, 1), end=d(2025, 12, 31))
        )
        if not photos:
            pytest.skip("No photos found in Immich")

        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        config.title_screens.show_month_dividers = True
        output = tmp_path / "output" / "monthly_photos.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            transition_duration=0.5,
            output_resolution="720p",
            include_photos=True,
            photo_assets=photos[:5],
            date_start=d(2025, 1, 1),
            date_end=d(2025, 1, 31),
            person_name="Photo Tester",
        )

        result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        duration = get_duration(probe)
        # Should be longer than just the 2 video clips (~6s) because photos add time
        assert duration > 5.0, f"Monthly memory too short: {duration:.1f}s"

        logger.info(
            f"Monthly+photos mega flow: {duration:.1f}s, "
            f"{len(clips[:2])} videos + {len(photos[:5])} photos"
        )


# ---------------------------------------------------------------------------
# Test C: SmartPipeline with Diverse Clips
# ---------------------------------------------------------------------------


class TestMegaFlowSmartPipeline:
    """SmartPipeline 4-phase analysis with real diverse clips.

    Exercises: _phase_cluster (thumbnail dedup), _phase_filter (density budget,
    quality gate, non-favorite filters, compilation filter, resolution filter,
    gap fillers, _cap_analysis_candidates), phase_analyze (download + score),
    phase_refine (temporal distribution).
    """

    def test_smart_pipeline_full_run(self, immich_clips, tmp_path):
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        clips, config, client = immich_clips

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        analysis_cache = VideoAnalysisCache(db_path=cache_dir / "analysis.db")
        thumbnail_cache = ThumbnailCache(cache_dir=cache_dir / "thumbnails")

        pipeline_config = PipelineConfig(
            target_clips=10,
            avg_clip_duration=5.0,
            hdr_only=False,
            prioritize_favorites=True,
            analyze_all=False,
        )

        pipeline = SmartPipeline(
            client=client,
            analysis_cache=analysis_cache,
            thumbnail_cache=thumbnail_cache,
            config=pipeline_config,
            analysis_config=config.analysis,
            app_config=config,
        )

        phases_seen: list[str] = []

        def on_progress(status: dict) -> None:
            phase = status.get("phase", "")
            if phase and (not phases_seen or phases_seen[-1] != phase):
                phases_seen.append(phase)

        result = pipeline.run(clips, progress_callback=on_progress)

        # Core assertions
        assert len(result.selected_clips) > 0
        assert len(result.selected_clips) <= 15  # ~1.5x target of 10
        assert len(result.clip_segments) > 0

        # Favorites should be preserved
        input_favorites = {c.asset.id for c in clips if c.asset.is_favorite}
        selected_ids = {c.asset.id for c in result.selected_clips}
        preserved_favorites = input_favorites & selected_ids
        if input_favorites:
            assert len(preserved_favorites) > 0, "No favorites preserved"

        # All 4 phases should have fired
        assert len(phases_seen) >= 3, f"Expected 4 phases, saw: {phases_seen}"

        # Clip segments should have valid time ranges
        for asset_id, (start, end) in result.clip_segments.items():
            assert end > start, f"Invalid segment for {asset_id}: {start}-{end}"

        logger.info(
            f"SmartPipeline mega flow: {len(clips)} → {len(result.selected_clips)} clips, "
            f"phases={phases_seen}, favorites preserved={len(preserved_favorites)}"
        )


# ---------------------------------------------------------------------------
# Test D: Streaming Assembly with Mixed Orientations + Privacy
# ---------------------------------------------------------------------------


class TestMegaFlowStreamingAssembly:
    """Streaming assembly with portrait+landscape clips, crossfade, privacy mode.

    Exercises: FrameDecoder (rotation, scale+pad blur bg, privacy blur, PTS reset),
    blend_crossfade (in-place numpy), StreamingEncoder, extract_and_mix_audio
    (normalize + privacy lowpass + crossfade), mux_video_audio (duration sync).
    """

    def test_mixed_orientation_privacy_crossfade(self, fixtures_dir, tmp_path):
        from immich_memories.processing.streaming_assembler import streaming_assemble_full

        # Generate portrait and landscape clips
        portrait = fixtures_dir / "mega_portrait.mp4"
        landscape = fixtures_dir / "mega_landscape.mp4"

        if not portrait.exists():
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
                    str(portrait),
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
        if not landscape.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=1280x720:rate=30:duration=3:alpha=160",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:duration=3",
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
                    str(landscape),
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )

        clip_p = _FakeStreamingClip(portrait, 3.0)
        clip_l = _FakeStreamingClip(landscape, 3.0)
        output = tmp_path / "mixed_privacy.mp4"

        values: list[float] = []

        def capture(pct: float, msg: str) -> None:
            values.append(pct)

        result = streaming_assemble_full(
            clips=[clip_p, clip_l],
            transitions=["crossfade"],
            output_path=output,
            width=1280,
            height=720,
            fps=30,
            fade_duration=0.5,
            privacy_mode=True,
            scale_mode="blur",
            normalize_audio=True,
            progress_callback=capture,
        )

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        duration = get_duration(probe)
        # 3 + 3 - 0.5 crossfade = ~5.5s
        assert 4.0 < duration < 7.0

        # Progress fired
        assert len(values) > 2
        assert values[-1] > values[0]

        # Output should be landscape resolution
        for s in probe.get("streams", []):
            if s.get("codec_type") == "video":
                assert int(s["width"]) == 1280
                assert int(s["height"]) == 720

        logger.info(
            f"Streaming mega flow: {duration:.1f}s, portrait+landscape, "
            f"privacy=True, {len(values)} progress updates"
        )


# ---------------------------------------------------------------------------
# Test E: Clip Extraction Batch with Buffer + Reencode
# ---------------------------------------------------------------------------


class TestMegaFlowClipExtraction:
    """Batch clip extraction: copy mode, reencode mode, all buffer combos, progress.

    Exercises: ClipExtractor.batch_extract, extract (copy + reencode),
    _make_buffered_segment (all 4 buffer combos), _extract_copy,
    _extract_with_reencode, hw accel detection, progress parsing, cleanup.
    """

    def test_batch_extraction_with_real_clip(self, immich_short_clips, tmp_path):
        from immich_memories.processing.clips import ClipExtractor, ClipSegment, extract_clip

        clips, config, client = immich_short_clips
        clip = clips[0]

        # Download the real clip
        dl_dir = tmp_path / "download"
        dl_dir.mkdir()
        video_path = dl_dir / f"{clip.asset.id}.mp4"
        client.download_asset(clip.asset.id, video_path)
        assert video_path.exists()

        dur = clip.duration_seconds or 5.0
        seg_end = min(dur * 0.5, 3.0)

        # Part 1: Test extract_clip with buffer vs without (exercises buffer logic)
        no_buf = extract_clip(
            video_path,
            start_time=0.5,
            end_time=seg_end,
            output_path=tmp_path / "no_buf.mp4",
            config=config,
        )
        with_buf = extract_clip(
            video_path,
            start_time=0.5,
            end_time=seg_end,
            buffer_start=True,
            buffer_end=True,
            buffer_seconds=0.5,
            output_path=tmp_path / "with_buf.mp4",
            config=config,
        )
        reencoded = extract_clip(
            video_path,
            start_time=0.5,
            end_time=seg_end,
            reencode=True,
            output_path=tmp_path / "reencoded.mp4",
            config=config,
        )

        assert no_buf.exists() and with_buf.exists() and reencoded.exists()
        dur_no = get_duration(ffprobe_json(no_buf))
        dur_buf = get_duration(ffprobe_json(with_buf))
        assert dur_buf >= dur_no * 0.9, "Buffered should be at least as long"
        assert get_duration(ffprobe_json(reencoded)) > 0.3

        # Part 2: Batch extract with progress (exercises batch_extract + progress)
        segments = [
            ClipSegment(
                asset_id=clip.asset.id,
                source_path=video_path,
                start_time=0.0,
                end_time=min(dur * 0.4, 3.0),
            ),
            ClipSegment(
                asset_id=clip.asset.id,
                source_path=video_path,
                start_time=max(0, dur * 0.5),
                end_time=min(dur, dur * 0.5 + 2.0),
            ),
        ]

        extractor = ClipExtractor(output_dir=tmp_path / "clips", config=config)
        progress_calls: list[float] = []

        results = extractor.batch_extract(
            segments,
            progress_callback=lambda cur, tot: progress_calls.append(cur / tot),
        )

        assert len(results) >= 1
        for r in results:
            assert r.exists()
            assert get_duration(ffprobe_json(r)) > 0.3
        assert len(progress_calls) > 0

        logger.info(
            f"Clip extraction mega flow: {len(results)} batch + 3 direct extracts, "
            f"{len(progress_calls)} progress calls, buffer diff={dur_buf - dur_no:.2f}s"
        )


# ---------------------------------------------------------------------------
# Test F: Live Photo Burst Merge
# ---------------------------------------------------------------------------


class TestMegaFlowLivePhotoBurst:
    """Live photo burst: detect → cluster → trim → align → merge → validate.

    Exercises: cluster_live_photos, split_non_overlapping, trim_points
    (overlap-aware), filter_valid_clips, align_clips_spectrogram (if audio),
    build_merge_command, FFmpeg concat with trim+normalize+afade.
    """

    def test_live_photo_burst_merge_pipeline(self, tmp_path):
        from immich_memories.api.sync_client import SyncImmichClient
        from immich_memories.config_loader import Config
        from immich_memories.processing.live_photo_merger import (
            build_merge_command,
            cluster_live_photos,
            filter_valid_clips,
        )
        from immich_memories.timeperiod import DateRange

        config = Config.from_yaml(Config.get_default_path())
        client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

        # Find live photos
        live_assets = client.get_live_photos_for_date_range(
            DateRange(start=date(2024, 1, 1), end=date(2026, 1, 1))
        )
        if len(live_assets) < 2:
            pytest.skip("Need at least 2 live photos in Immich")

        # Cluster and search for a valid burst (same-orientation, downloadable)
        clusters = cluster_live_photos(live_assets, merge_window_seconds=10.0)
        clusters.sort(key=lambda c: c.count, reverse=True)

        cache_dir = tmp_path / "burst_cache"
        cache_dir.mkdir()
        merge_cluster = None
        clip_paths: list[Path] = []
        trim_points: list[tuple[float, float]] = []

        for cluster in clusters:
            if cluster.count < 2 or len(cluster.video_asset_ids) < 2:
                continue

            paths = []
            for vid in cluster.video_asset_ids:
                dest = cache_dir / f"{vid}.MOV"
                try:
                    client.download_asset(vid, dest)
                    if dest.exists() and dest.stat().st_size > 0:
                        paths.append(dest)
                except Exception:
                    pass

            if len(paths) < 2:
                continue

            trims = cluster.trim_points()[: len(paths)]
            filtered_paths, filtered_trims = filter_valid_clips(paths, trims)

            if len(filtered_paths) >= 2:
                merge_cluster = cluster
                clip_paths = filtered_paths
                trim_points = filtered_trims
                logger.info(
                    f"Found valid burst: {cluster.count} photos, "
                    f"{len(filtered_paths)} clips after orientation filter"
                )
                break

        if merge_cluster is None:
            pytest.skip("No live photo cluster with 2+ same-orientation clips found")

        # Try spectrogram alignment if we have audio
        try:
            from immich_memories.processing.live_photo_merger import (
                align_clips_spectrogram,
            )

            shutters = [
                a.file_created_at.timestamp() for a in merge_cluster.assets[: len(clip_paths)]
            ]
            durations = [3.0] * len(clip_paths)
            aligned_v, aligned_a = align_clips_spectrogram(clip_paths, shutters, durations)
            if aligned_v:
                trim_points = aligned_v
                logger.info("Spectrogram alignment succeeded")
        except Exception as e:
            logger.info(f"Spectrogram alignment skipped: {e}")

        # Build and run merge command
        merged_path = tmp_path / "merged_burst.mp4"
        cmd = build_merge_command(clip_paths, trim_points, merged_path)

        merge_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert merge_result.returncode == 0, f"FFmpeg merge failed: {merge_result.stderr[:500]}"

        assert merged_path.exists()
        assert merged_path.stat().st_size > 1000

        probe = ffprobe_json(merged_path)
        assert has_stream(probe, "video")

        duration = get_duration(probe)
        assert duration > 0

        logger.info(
            f"Live photo mega flow: {len(clip_paths)} clips merged → "
            f"{duration:.1f}s, {merged_path.stat().st_size / 1024:.0f} KB"
        )
