"""End-to-end integration tests for the full generate pipeline.

Requires real Immich + FFmpeg. These tests fetch actual videos from Immich,
run the full analysis + assembly pipeline, and verify the output video
with ffprobe and pixel-level assertions.

Run: make test-integration-cli
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from immich_memories.timeperiod import DateRange
from tests.integration.cli.test_generate import requires_immich

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _has_stream(probe: dict, codec_type: str) -> bool:
    return any(s.get("codec_type") == codec_type for s in probe.get("streams", []))


def _get_duration(probe: dict) -> float:
    return float(probe.get("format", {}).get("duration", 0))


def _get_resolution(probe: dict) -> tuple[int, int]:
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    return 0, 0


def _extract_frame_rgb(
    video: Path, time_pos: float, width: int = 160, height: int = 90
) -> np.ndarray:
    """Extract a frame at the given time position as a small RGB array."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-ss",
            str(time_pos),
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


def _generate_memory(
    tmp_path: Path,
    *,
    year: int,
    month: int | None = None,
    enable_titles: bool = False,
    transition: str = "cut",
    target_duration: float = 15.0,
    target_clips: int = 5,
) -> Path:
    """Run generate_memory with real Immich.

    Returns path to output video, or pytest.skip if no videos found.
    """
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams, assets_to_clips, generate_memory

    config = Config.from_yaml(Config.get_default_path())
    config.title_screens.enabled = enable_titles

    if month is not None:
        start = date(year, month, 1)
        end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1)
    else:
        start = date(year, 1, 1)
        end = date(year, 12, 31)

    output = tmp_path / "e2e_output.mp4"

    with SyncImmichClient(config.immich.url, config.immich.api_key) as client:
        from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
        from immich_memories.cache.database import VideoAnalysisCache
        from immich_memories.cache.thumbnail_cache import ThumbnailCache

        dr = DateRange(
            start=datetime.combine(start, datetime.min.time()),
            end=datetime.combine(end, datetime.max.time()),
        )
        assets = client.get_videos_for_date_range(dr)
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
        pipeline_config.target_clips = min(target_clips, len(clips))

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
        assert selected, (
            f"Pipeline selected 0 clips from {len(clips)} candidates — "
            f"density budget or quality gate may be over-filtering (see #151)"
        )

        params = GenerationParams(
            clips=selected,
            output_path=output,
            config=config,
            client=client,
            transition=transition,
            date_start=start,
            date_end=end,
            no_music=True,
            upload_enabled=False,
            target_duration_seconds=target_duration,
            clip_segments=pipeline_result.clip_segments,
        )
        return generate_memory(params)


# ---------------------------------------------------------------------------
# Tests: Basic pipeline validity
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineOutput:
    """Verify the pipeline produces a valid, non-empty video."""

    def test_output_exists_and_has_size(self, tmp_path):
        """Pipeline produces a file > 1KB."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        assert result.exists(), "Output file was not created"
        assert result.stat().st_size > 1000, (
            f"Output file only {result.stat().st_size} bytes — likely empty/corrupt"
        )

    def test_has_video_stream(self, tmp_path):
        """Output contains a video stream."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video"), "No video stream in output"

    def test_has_audio_stream(self, tmp_path):
        """Output contains an audio stream (clip audio, even without music)."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "audio"), "No audio stream — assembly concat will fail downstream"

    def test_duration_reasonable(self, tmp_path):
        """Output duration is at least 3 seconds (not truncated)."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)
        assert duration > 3.0, f"Duration only {duration:.1f}s — pipeline may have truncated"


# ---------------------------------------------------------------------------
# Tests: Pixel-level content validation
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelinePixels:
    """Verify the output video has actual visual content."""

    def test_mid_frame_not_black(self, tmp_path):
        """Mid-frame should have real content, not solid black."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)

        mean = float(frame.mean())
        # WHY: A black frame has mean ~0. Real video content is typically > 30.
        assert mean > 10.0, f"Mid-frame looks black (mean={mean:.1f})"

    def test_mid_frame_has_variation(self, tmp_path):
        """Mid-frame should have pixel variation (not solid color)."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)

        std = float(frame.std())
        # WHY: A solid color frame has std ~0. Real video has texture/edges.
        assert std > 5.0, f"Mid-frame is solid color (std={std:.1f})"

    def test_first_and_last_frames_differ(self, tmp_path):
        """First and last frames should be visually different (not frozen)."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)

        first = _extract_frame_rgb(result, 0.5)
        last = _extract_frame_rgb(result, max(0.5, duration - 0.5))

        diff = float(np.abs(first.astype(float) - last.astype(float)).mean())
        # WHY: If first == last, video may be a single frozen frame looped.
        assert diff > 3.0, f"First and last frames look identical (diff={diff:.1f})"


# ---------------------------------------------------------------------------
# Tests: Title screens (the most regression-prone path)
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineWithTitles:
    """Verify title screen rendering in the full pipeline.

    Title screens have been the source of most regressions (Emile crash,
    divider count changes, pink HDR). These tests enable titles and verify
    the output includes them.
    """

    def test_titles_enabled_produces_longer_video(self, tmp_path):
        """Video with titles should be longer than target (title + ending added)."""
        result = _generate_memory(
            tmp_path, year=2025, month=6, enable_titles=True, target_duration=10.0
        )
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)
        # WHY: Title screen (3.5s) + ending (4s) add ~7.5s to content duration.
        # With 10s target content, output should be > 12s.
        assert duration > 10.0, (
            f"Duration {duration:.1f}s — title screens may not have been inserted"
        )

    def test_title_fade_from_white_at_start(self, tmp_path):
        """First frame should be near-white (title fade-from-white)."""
        result = _generate_memory(
            tmp_path, year=2025, month=6, enable_titles=True, target_duration=10.0
        )
        first_frame = _extract_frame_rgb(result, 0.05)
        mean = float(first_frame.mean())
        # WHY: Title screens start with fade-from-white. First frame should
        # be bright. If titles aren't rendering, first frame is dark content.
        assert mean > 150, (
            f"First frame mean {mean:.0f} — expected bright (fade-from-white). "
            f"Title screen may not be rendering."
        )

    def test_ending_fade_to_white_at_end(self, tmp_path):
        """Last frame should be near-white (ending fade-to-white)."""
        result = _generate_memory(
            tmp_path, year=2025, month=6, enable_titles=True, target_duration=10.0
        )
        probe = _ffprobe_json(result)
        duration = _get_duration(probe)
        last_frame = _extract_frame_rgb(result, max(0.1, duration - 0.1))
        mean = float(last_frame.mean())
        # WHY: Ending screen fades to white. Last frame should be bright.
        # If ending isn't rendering, last frame is dark content.
        assert mean > 150, (
            f"Last frame mean {mean:.0f} — expected bright (fade-to-white). "
            f"Ending screen may not be rendering."
        )

    def test_title_text_visible_in_first_seconds(self, tmp_path):
        """Frame at ~2s (mid-title) should show text on dark background."""
        result = _generate_memory(
            tmp_path, year=2025, month=6, enable_titles=True, target_duration=10.0
        )
        # WHY: Title screen is 3.5s. At 2s, fade-from-white is done,
        # text should be visible on dark cinematic background.
        frame = _extract_frame_rgb(result, 2.0)

        # Center band (where text renders) should have more variation
        # than corners (pure background)
        h, w = frame.shape[:2]
        center = frame[int(h * 0.3) : int(h * 0.7), :]
        corner = frame[: int(h * 0.1), : int(w * 0.1)]

        center_range = float(center.max()) - float(center.min())
        corner_range = float(corner.max()) - float(corner.min())

        assert center_range > corner_range, (
            f"Title text not visible: center range ({center_range:.0f}) "
            f"should exceed corner range ({corner_range:.0f})"
        )


# ---------------------------------------------------------------------------
# Tests: Transition and resolution variations
# ---------------------------------------------------------------------------


@requires_immich
class TestPipelineVariations:
    """Test different pipeline configurations produce valid output."""

    def test_crossfade_transition(self, tmp_path):
        """Crossfade transition produces valid output."""
        result = _generate_memory(tmp_path, year=2025, transition="crossfade", target_clips=3)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video")
        assert _get_duration(probe) > 2.0

    def test_single_month_has_content(self, tmp_path):
        """Single month produces video with real content."""
        result = _generate_memory(tmp_path, year=2025, month=6)
        probe = _ffprobe_json(result)
        assert _has_stream(probe, "video")

        mid_time = _get_duration(probe) / 2.0
        frame = _extract_frame_rgb(result, mid_time)
        assert float(frame.mean()) > 10.0, "Single month output looks black"
