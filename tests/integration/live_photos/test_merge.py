"""Integration tests for live photo burst merging with spectrogram alignment.

Real Immich reads, real FFmpeg, real spectrogram alignment. No mocks.
Skips gracefully if Immich not reachable or no live photos found.

Run: make test-integration
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _has_immich() -> bool:
    try:
        from immich_memories.config_loader import Config

        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False
        import httpx

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(not _has_immich(), reason="Immich not reachable")


@pytest.fixture(scope="module")
def live_photo_burst():
    """Find a real overlapping burst (3+ clips) from Immich. Module-scoped."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.processing.live_photo_merger import cluster_live_photos
    from immich_memories.timeperiod import DateRange

    config = Config.from_yaml(Config.get_default_path())
    client = SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key)

    live = client.get_live_photos_for_date_range(
        DateRange(start=date(2024, 1, 1), end=date(2026, 3, 31))
    )

    if len(live) < 3:
        pytest.skip("Need at least 3 live photos in Immich")

    # Tight window (3s) = only truly overlapping clips
    clusters = cluster_live_photos(live, merge_window_seconds=3.0)

    # Find a clean burst (no duplicate IDs, 3+ clips)
    for b in sorted(clusters, key=lambda c: c.count, reverse=True):
        ids = [a.live_photo_video_id for a in b.assets if a.live_photo_video_id]
        if len(ids) != len(set(ids)) or len(ids) < 3:
            continue
        gaps = [
            (b.assets[i + 1].file_created_at - b.assets[i].file_created_at).total_seconds()
            for i in range(b.count - 1)
        ]
        if any(g < 0.3 for g in gaps):
            continue
        return b, config, client

    pytest.skip("No clean 3+ live photo bursts found in Immich")
    return None  # unreachable, makes mypy happy


@pytest.fixture(scope="module")
def second_live_photo_burst(live_photo_burst):
    """Find a SECOND different burst for more test coverage."""
    from immich_memories.processing.live_photo_merger import cluster_live_photos
    from immich_memories.timeperiod import DateRange

    first_burst, config, client = live_photo_burst
    first_id = first_burst.assets[0].id

    live = client.get_live_photos_for_date_range(
        DateRange(start=date(2024, 1, 1), end=date(2026, 3, 31))
    )
    clusters = cluster_live_photos(live, merge_window_seconds=3.0)

    for b in sorted(clusters, key=lambda c: c.count, reverse=True):
        if b.assets[0].id == first_id:
            continue
        ids = [a.live_photo_video_id for a in b.assets if a.live_photo_video_id]
        if len(ids) != len(set(ids)) or len(ids) < 3:
            continue
        gaps = [
            (b.assets[i + 1].file_created_at - b.assets[i].file_created_at).total_seconds()
            for i in range(b.count - 1)
        ]
        if any(g < 0.3 for g in gaps):
            continue
        return b, config, client

    pytest.skip("Only one clean burst found")
    return None


@requires_immich
class TestLivePhotoSpectrogram:
    """End-to-end spectrogram-aligned burst merge with real Immich data."""

    def test_spectrogram_alignment_produces_valid_video(self, live_photo_burst, tmp_path):
        """Full pipeline: fetch → cluster → align → merge → valid video."""
        import json

        from immich_memories.processing.live_photo_merger import (
            align_clips_spectrogram,
            build_merge_command,
        )

        burst, config, client = live_photo_burst
        burst_ids = [a.live_photo_video_id for a in burst.assets if a.live_photo_video_id]
        shutters = [a.file_created_at.timestamp() for a in burst.assets[: len(burst_ids)]]

        # Download clips
        clip_paths: list[Path] = []
        durations: list[float] = []
        for vid in burst_ids:
            dest = tmp_path / f"{vid[:8]}.MOV"
            client.download_asset(vid, dest)
            clip_paths.append(dest)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            durations.append(float(json.loads(probe.stdout)["format"]["duration"]))

        # Spectrogram alignment
        video_trims, audio_trims = align_clips_spectrogram(clip_paths, shutters, durations)

        assert len(video_trims) == len(clip_paths)
        assert len(audio_trims) == len(clip_paths)

        # Filter out invalid segments (non-overlapping pairs produce negative durations)
        valid = [
            (i, v, a)
            for i, (v, a) in enumerate(zip(video_trims, audio_trims, strict=True))
            if v[1] > v[0]
        ]
        assert len(valid) >= 2, "Need at least 2 valid segments after filtering"
        valid_paths = [clip_paths[i] for i, _, _ in valid]
        video_trims = [v for _, v, _ in valid]
        audio_trims = [a for _, _, a in valid]

        # Merge
        merged = tmp_path / "merged.mp4"
        cmd = build_merge_command(valid_paths, video_trims, merged, audio_trim_points=audio_trims)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[:300]}"

        # Verify output
        assert merged.exists()
        assert merged.stat().st_size > 1000

        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        probe = ffprobe_json(merged)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 0

        # Duration should be reasonable (not 0, not 10x the input)
        total_input = sum(durations)
        output_dur = get_duration(probe)
        assert output_dur > 1.0, "Output too short"
        assert output_dur < total_input, (
            "Output should be shorter than sum of inputs (overlaps removed)"
        )

    def test_generate_memory_with_live_burst(self, live_photo_burst, tmp_path):
        """generate_memory() handles live photo bursts end-to-end."""
        from immich_memories.api.models import VideoClipInfo
        from immich_memories.generate import GenerationParams, generate_memory

        burst, config, client = live_photo_burst
        burst_ids = [a.live_photo_video_id for a in burst.assets if a.live_photo_video_id]
        shutters = [a.file_created_at.timestamp() for a in burst.assets[: len(burst_ids)]]

        clip = VideoClipInfo(
            asset=burst.assets[0],
            duration_seconds=burst.estimated_duration,
            live_burst_video_ids=burst_ids,
            live_burst_trim_points=burst.trim_points(),
            live_burst_shutter_timestamps=shutters,
        )

        config.title_screens.enabled = False
        output = tmp_path / "live_memory.mp4"

        params = GenerationParams(
            clips=[clip],
            output_path=output,
            config=config,
            client=client,
            transition="cut",
            upload_enabled=False,
        )

        result = generate_memory(params)

        assert result.exists()
        assert result.stat().st_size > 1000

        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 1.0

    def test_second_burst_also_merges(self, second_live_photo_burst, tmp_path):
        """A different burst also merges successfully (tests variety)."""
        import json

        from immich_memories.processing.live_photo_merger import (
            align_clips_spectrogram,
            build_merge_command,
        )

        burst, config, client = second_live_photo_burst
        burst_ids = [a.live_photo_video_id for a in burst.assets if a.live_photo_video_id]
        shutters = [a.file_created_at.timestamp() for a in burst.assets[: len(burst_ids)]]

        clip_paths: list[Path] = []
        durations: list[float] = []
        for vid in burst_ids:
            dest = tmp_path / f"{vid[:8]}.MOV"
            client.download_asset(vid, dest)
            clip_paths.append(dest)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            durations.append(float(json.loads(probe.stdout)["format"]["duration"]))

        video_trims, audio_trims = align_clips_spectrogram(clip_paths, shutters, durations)

        # Filter invalid segments
        valid = [
            (i, v, a)
            for i, (v, a) in enumerate(zip(video_trims, audio_trims, strict=True))
            if v[1] > v[0]
        ]
        assert len(valid) >= 2
        valid_paths = [clip_paths[i] for i, _, _ in valid]
        v_trims = [v for _, v, _ in valid]
        a_trims = [a for _, _, a in valid]

        merged = tmp_path / "second_burst.mp4"
        cmd = build_merge_command(valid_paths, v_trims, merged, audio_trim_points=a_trims)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"FFmpeg failed: {result.stderr[:300]}"
        assert merged.exists()

        from tests.integration.conftest import ffprobe_json, get_duration, has_stream

        assert has_stream(ffprobe_json(merged), "video")
        assert get_duration(ffprobe_json(merged)) > 1.0
