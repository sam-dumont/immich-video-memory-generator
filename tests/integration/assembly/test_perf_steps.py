"""Per-step timing benchmark for the assembly pipeline.

Measures wall time, CPU time, and memory per pipeline step using
synthetic clips (no Immich needed). Run with: make benchmark-steps

Exports per-step results to tests/perf-results-steps.json for
cross-platform comparison (Mac Metal vs Linux GPU vs CPU-only).
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import make_clip
from tests.integration.assembly.perf_utils import PerfResult, measure_resources, save_results
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

logger = logging.getLogger("test.perf.steps")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

_step_results: list[PerfResult] = []


def _make_test_clip(path: Path, asset_id: str = "test") -> object:
    """Create a VideoClipInfo pointing to a real local file."""
    clip = make_clip(asset_id, duration=3.0, width=1280, height=720)
    clip.local_path = str(path)
    return clip


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    """Extract (width, height) from ffprobe data."""
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _extract_frame_pixels(video_path: Path, timestamp: float) -> np.ndarray:
    """Extract a single frame at timestamp as grayscale numpy array."""
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
        return np.array([], dtype=np.uint8)
    return np.frombuffer(result.stdout, dtype=np.uint8)


class TestPerStepTiming:
    """Measure wall/CPU time per pipeline step with synthetic clips.

    Each test measures a different pipeline configuration and validates
    real output: resolution, duration, pixel content, audio streams.
    """

    def test_assembly_only_step(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Time assembly-only (no titles, no music). Verify 720p output and content."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(test_clip_720p, "step-a")
        clip_b = _make_test_clip(test_clip_720p_b, "step-b")

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "step_assembly.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
        )

        with measure_resources("assembly_only", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 3.0, f"Duration too short: {duration:.1f}s"

        # Verify output is exactly 720p
        w, h = _get_resolution(probe)
        assert w == 1280 and h == 720, f"Expected 720p (1280x720), got {w}x{h}"

        # Verify video content is not blank (testsrc2 produces non-black frames)
        pixels = _extract_frame_pixels(final, 1.0)
        assert len(pixels) > 0, "Failed to extract frame at t=1s"
        assert float(np.mean(pixels)) > 10.0, "Frame at t=1s appears blank"
        # Verify spatial detail exists (not a flat color)
        assert float(np.std(pixels)) > 5.0, "Frame lacks spatial detail (flat color?)"

        logger.info(result.summary_line)
        _step_results.append(result)

    def test_title_rendering_step(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Time assembly with title screens. Verify title frame has visible content."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(test_clip_720p, "title-a")
        clip_b = _make_test_clip(test_clip_720p_b, "title-b")

        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "step_titles.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            memory_type="month",
        )

        with measure_resources("with_titles", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 6.0, f"Duration too short for titled video: {duration:.1f}s"

        # Verify output resolution matches target
        w, h = _get_resolution(probe)
        assert w == 1280 and h == 720, f"Expected 720p (1280x720), got {w}x{h}"

        # Title frame at t=0.5 should have visible content (not all-black)
        title_pixels = _extract_frame_pixels(final, 0.5)
        assert len(title_pixels) > 0, "Failed to extract title frame"
        assert float(np.mean(title_pixels)) > 5.0, (
            f"Title frame appears blank (mean={float(np.mean(title_pixels)):.1f})"
        )

        # Content frame (past title) should have different visual signature
        content_pixels = _extract_frame_pixels(final, 4.0)
        assert len(content_pixels) > 0, "Failed to extract content frame"
        assert float(np.std(content_pixels)) > 5.0, "Content frame lacks detail"

        logger.info(result.summary_line)
        _step_results.append(result)

    def test_music_mixing_step(self, test_clip_720p, test_clip_720p_b, test_music_short, tmp_path):
        """Time assembly with music mixing. Verify audio stream present and reasonable."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(test_clip_720p, "music-a")
        clip_b = _make_test_clip(test_clip_720p_b, "music-b")

        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "step_music.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
            music_path=test_music_short,
            music_volume=0.5,
        )

        with measure_resources("with_music", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio"), "Music mixing should produce audio stream"

        # Audio and video durations should be close
        video_dur = get_duration(probe)
        audio_dur = 0.0
        for s in probe.get("streams", []):
            if s.get("codec_type") == "audio" and "duration" in s:
                audio_dur = float(s["duration"])
        if audio_dur > 0:
            assert abs(audio_dur - video_dur) < 1.0, (
                f"Audio/video duration mismatch: audio={audio_dur:.1f}s, video={video_dur:.1f}s"
            )

        logger.info(result.summary_line)
        _step_results.append(result)

    def test_full_pipeline_step(self, test_clip_720p, test_clip_720p_b, test_music_short, tmp_path):
        """Time full pipeline: titles + music + assembly. Verify all output properties."""
        from immich_memories.config_loader import Config
        from immich_memories.generate import GenerationParams, generate_memory

        clip_a = _make_test_clip(test_clip_720p, "full-a")
        clip_b = _make_test_clip(test_clip_720p_b, "full-b")

        config = Config()
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "step_full.mp4"

        params = GenerationParams(
            clips=[clip_a, clip_b],
            output_path=output,
            config=config,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
            music_path=test_music_short,
            music_volume=0.5,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            memory_type="month",
        )

        with measure_resources("full_pipeline", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio"), "Full pipeline should include audio"
        duration = get_duration(probe)
        assert duration > 6.0, f"Full pipeline duration too short: {duration:.1f}s"

        # Verify 720p output
        w, h = _get_resolution(probe)
        assert w == 1280 and h == 720, f"Expected 720p (1280x720), got {w}x{h}"

        # Title frame visible
        title_pixels = _extract_frame_pixels(final, 0.5)
        assert len(title_pixels) > 0, "Failed to extract title frame"
        assert float(np.mean(title_pixels)) > 5.0, "Title frame appears blank"

        # Content frame has spatial detail
        content_pixels = _extract_frame_pixels(final, 4.0)
        assert len(content_pixels) > 0, "Failed to extract content frame"
        assert float(np.std(content_pixels)) > 5.0, "Content frame lacks detail"

        # Output file is a reasonable size (not empty or suspiciously small)
        assert result.output_size_mb > 0.01, f"Output too small: {result.output_size_mb:.3f}MB"

        logger.info(result.summary_line)
        _step_results.append(result)


def test_save_step_results(tmp_path):
    """Save collected per-step results to JSON after all scenarios complete."""
    if not _step_results:
        pytest.skip("No step results collected")

    logger.info("=" * 60)
    logger.info("PER-STEP PERFORMANCE SUMMARY")
    logger.info("=" * 60)
    for r in _step_results:
        logger.info(r.summary_line)
    logger.info("=" * 60)

    # Compute title rendering overhead by subtracting assembly-only from with-titles
    asm_only = next((r for r in _step_results if r.scenario == "assembly_only"), None)
    with_titles = next((r for r in _step_results if r.scenario == "with_titles"), None)
    if asm_only and with_titles:
        title_overhead = with_titles.wall_seconds - asm_only.wall_seconds
        logger.info(f"DERIVED: title_rendering_overhead = {title_overhead:.1f}s")

    with_music = next((r for r in _step_results if r.scenario == "with_music"), None)
    if asm_only and with_music:
        music_overhead = with_music.wall_seconds - asm_only.wall_seconds
        logger.info(f"DERIVED: music_mixing_overhead = {music_overhead:.1f}s")

    project_root = Path(__file__).resolve().parents[3]
    output = project_root / "tests" / "perf-results-steps.json"
    save_results(_step_results, output)
    logger.info(f"Results saved to {output}")
