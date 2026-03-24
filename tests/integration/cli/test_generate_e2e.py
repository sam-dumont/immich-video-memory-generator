"""End-to-end integration tests for the full generate pipeline.

Requires real Immich + FFmpeg. These tests fetch actual videos from Immich,
run the full analysis + assembly pipeline, and verify the output video
with ffprobe and pixel-level assertions.

Run: make test-integration-cli
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from tests.integration.cli.test_generate import requires_immich

pytestmark = [pytest.mark.integration]


def _ffprobe_json(path: Path) -> dict:
    """Run ffprobe and return parsed JSON with all streams + format."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def _has_video_stream(probe: dict) -> bool:
    return any(s.get("codec_type") == "video" for s in probe.get("streams", []))


def _has_audio_stream(probe: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


def _get_duration(probe: dict) -> float:
    return float(probe.get("format", {}).get("duration", 0))


def _extract_mid_frame_rgb(video: Path, width: int = 160, height: int = 90) -> np.ndarray:
    """Extract the frame at the midpoint of the video as a small RGB array."""
    probe = _ffprobe_json(video)
    dur = _get_duration(probe)
    mid_time = dur / 2.0

    result = subprocess.run(
        [
            "ffmpeg",
            "-ss",
            str(mid_time),
            "-i",
            str(video),
            "-vf",
            f"scale={width}:{height}",
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"Frame extraction failed: {stderr[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)


def _generate_short_memory(tmp_path: Path, *, year: int, month: int | None = None) -> Path:
    """Run generate_memory with real Immich, minimal settings for speed."""
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams, generate_memory

    config = Config.from_yaml(Config.get_default_path())
    config.title_screens.enabled = False

    if month is not None:
        start = date(year, month, 1)
        end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1)
    else:
        start = date(year, 1, 1)
        end = date(year, 12, 31)

    output = tmp_path / "e2e_output.mp4"

    with SyncImmichClient(config.immich.url, config.immich.api_key) as client:
        from datetime import datetime

        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.cache.thumbnail_cache import ThumbnailCache
        from immich_memories.generate import assets_to_clips

        assets = client.get_videos_for_date_range(
            datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.max.time()),
        )
        if not assets:
            pytest.skip(f"No videos in Immich for {start} to {end}")

        clips = assets_to_clips(assets)
        if not clips:
            pytest.skip("All clips too short after filtering")

        analysis_cache = VideoAnalysisCache(db_path=config.cache.database_path)
        thumbnail_cache = ThumbnailCache(cache_dir=config.cache.cache_path / "thumbnails")
        pipeline_config = PipelineConfig(
            hdr_only=False,
            prioritize_favorites=True,
            analysis_depth="fast",
        )
        # Small target for speed
        pipeline_config.target_clips = min(5, len(clips))

        pipeline = SmartPipeline(
            client=client,
            analysis_cache=analysis_cache,
            thumbnail_cache=thumbnail_cache,
            config=pipeline_config,
            analysis_config=config.analysis,
            app_config=config,
        )
        pipeline_result = pipeline.run(clips)
        selected = pipeline_result.selected_clips
        if not selected:
            pytest.skip("Pipeline selected no clips")

        params = GenerationParams(
            clips=selected,
            output_path=output,
            config=config,
            client=client,
            transition="cut",
            date_start=start,
            date_end=end,
            no_music=True,
            upload_enabled=False,
            target_duration_seconds=15.0,
            clip_segments=pipeline_result.clip_segments,
        )
        return generate_memory(params)


@requires_immich
class TestRealPipelineIntegration:
    """End-to-end tests with real Immich + FFmpeg.

    These tests connect to Immich, fetch actual videos, run the full
    analysis + assembly pipeline, and verify the output video.
    """

    def test_year_in_review_produces_valid_video(self, tmp_path):
        """Full pipeline: fetch -> analyze -> assemble -> valid mp4 with content."""
        result = _generate_short_memory(tmp_path, year=2025)

        assert result.exists(), "Output file was not created"
        assert result.stat().st_size > 1000, "Output file suspiciously small"

        probe = _ffprobe_json(result)
        assert _has_video_stream(probe), "No video stream in output"
        duration = _get_duration(probe)
        assert duration > 3.0, f"Duration too short: {duration}s"

    def test_single_month_produces_valid_video(self, tmp_path):
        """Single month pipeline produces shorter but valid output."""
        result = _generate_short_memory(tmp_path, year=2025, month=6)

        assert result.exists()
        probe = _ffprobe_json(result)
        assert _has_video_stream(probe)
        duration = _get_duration(probe)
        assert duration > 2.0, f"Duration too short: {duration}s"

    def test_output_has_audio_stream(self, tmp_path):
        """Output must have audio (clip audio even without music)."""
        result = _generate_short_memory(tmp_path, year=2025)

        probe = _ffprobe_json(result)
        assert _has_audio_stream(probe), "Output missing audio stream"

    def test_output_not_black(self, tmp_path):
        """Mid-frame pixel check: output has actual content, not black."""
        result = _generate_short_memory(tmp_path, year=2025)

        frame = _extract_mid_frame_rgb(result)
        mean_brightness = float(frame.mean())
        # A fully black frame would be 0.0; real video content should be well above
        assert mean_brightness > 10.0, f"Mid-frame looks black (mean={mean_brightness:.1f})"
        # Also check there's some variation (not a solid color)
        std = float(frame.std())
        assert std > 5.0, f"Mid-frame has no variation (std={std:.1f})"
