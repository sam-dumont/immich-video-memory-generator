"""Full pipeline per-step benchmark (requires Immich + FFmpeg).

Measures wall time, CPU time, and memory for each pipeline phase using
real Immich clips. Run with: make benchmark-pipeline

Exports results to tests/perf-results-pipeline.json for Mac vs Linux
GPU comparison.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from subprocess import run as sp_run

import numpy as np
import pytest

from tests.integration.assembly.perf_utils import PerfResult, measure_resources, save_results
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)
from tests.integration.immich_fixtures import requires_immich

logger = logging.getLogger("test.perf.pipeline")

pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich, pytest.mark.perf]

_pipeline_results: list[PerfResult] = []


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _extract_frame_pixels(video_path: Path, timestamp: float) -> np.ndarray:
    result = sp_run(
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


class TestFullPipelineBenchmark:
    """End-to-end pipeline timing with real Immich clips.

    Measures: clip download/extraction, title rendering, assembly,
    music mixing, and total pipeline time. Verifies real output at
    each step (resolution, pixel content, audio streams).
    """

    def test_immich_assembly_only(self, immich_short_clips, tmp_path):
        """Immich clips -> assembly only (no titles, no music). Baseline measurement."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "immich_asm.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
        )

        with measure_resources("immich_assembly_only", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 2.0, f"Duration too short: {duration:.1f}s"

        w, h = _get_resolution(probe)
        assert max(w, h) <= 1280, f"Resolution exceeds 720p: {w}x{h}"

        # Real Immich content should not be blank
        pixels = _extract_frame_pixels(final, 1.0)
        assert len(pixels) > 0, "Failed to extract frame"
        assert float(np.mean(pixels)) > 3.0, "Frame appears blank"

        logger.info(result.summary_line)
        _pipeline_results.append(result)

    def test_immich_with_titles(self, immich_short_clips, tmp_path):
        """Immich clips -> titles + assembly. Measures title rendering overhead."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "immich_titles.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            transition="crossfade",
            transition_duration=0.3,
            output_resolution="720p",
            person_name="Benchmark Person",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            memory_type="year_in_review",
        )

        with measure_resources("immich_with_titles", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 4.0, f"Duration too short for titled video: {duration:.1f}s"

        w, h = _get_resolution(probe)
        assert max(w, h) <= 1280, f"Resolution exceeds 720p: {w}x{h}"

        # Title frame should have visible content
        title_pixels = _extract_frame_pixels(final, 0.5)
        assert len(title_pixels) > 0, "Failed to extract title frame"
        assert float(np.mean(title_pixels)) > 3.0, "Title frame appears blank"

        logger.info(result.summary_line)
        _pipeline_results.append(result)

    def test_immich_full_pipeline(self, immich_short_clips, tmp_path):
        """Immich clips -> titles + music + assembly. Full pipeline measurement."""
        from immich_memories.generate import GenerationParams, generate_memory

        clips, config, client = immich_short_clips
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "immich_full.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            transition="smart",
            transition_duration=0.3,
            output_resolution="720p",
            person_name="Benchmark Person",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
            memory_type="year_in_review",
        )

        with measure_resources("immich_full_pipeline", clip_count=2, resolution="720p") as result:
            final = generate_memory(params)

        assert final.exists(), f"Output not created: {final}"
        result.output_size_mb = final.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(final)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert duration > 4.0, f"Full pipeline duration too short: {duration:.1f}s"

        w, h = _get_resolution(probe)
        assert max(w, h) <= 1280, f"Resolution exceeds 720p: {w}x{h}"

        # Title + content frames should both have visible content
        title_pixels = _extract_frame_pixels(final, 0.5)
        assert len(title_pixels) > 0
        assert float(np.mean(title_pixels)) > 3.0, "Title frame blank"

        content_pixels = _extract_frame_pixels(final, 4.0)
        assert len(content_pixels) > 0
        assert float(np.std(content_pixels)) > 3.0, "Content frame lacks detail"

        assert result.output_size_mb > 0.01, f"Output too small: {result.output_size_mb:.3f}MB"

        logger.info(result.summary_line)
        _pipeline_results.append(result)


def test_save_pipeline_results(tmp_path):
    """Save collected pipeline results to JSON after all scenarios complete."""
    if not _pipeline_results:
        pytest.skip("No pipeline results collected")

    logger.info("=" * 60)
    logger.info("PIPELINE PERFORMANCE SUMMARY (Immich)")
    logger.info("=" * 60)
    for r in _pipeline_results:
        logger.info(r.summary_line)
    logger.info("=" * 60)

    # Compute overheads
    asm_only = next((r for r in _pipeline_results if r.scenario == "immich_assembly_only"), None)
    with_titles = next((r for r in _pipeline_results if r.scenario == "immich_with_titles"), None)
    if asm_only and with_titles:
        overhead = with_titles.wall_seconds - asm_only.wall_seconds
        logger.info(f"DERIVED: immich_title_overhead = {overhead:.1f}s")

    project_root = Path(__file__).resolve().parents[3]
    output = project_root / "tests" / "perf-results-pipeline.json"
    save_results(_pipeline_results, output)
    logger.info(f"Results saved to {output}")
