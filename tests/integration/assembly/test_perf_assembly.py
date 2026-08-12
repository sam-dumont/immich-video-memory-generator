"""Performance benchmarks for video assembly.

Measures Python peak memory, wall time, and CPU time for realistic
assembly scenarios. Run with: make benchmark-perf

Results are logged as structured PERF: lines and optionally saved
to tests/perf-results.json for regression tracking.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from immich_memories.processing.assembly_config import AssemblySettings, TransitionType
from immich_memories.processing.video_assembler import VideoAssembler
from tests.integration.assembly.conftest import make_n_clips
from tests.integration.assembly.perf_utils import (
    BenchmarkSummary,
    run_repetitions,
    save_benchmark_json,
    save_summary_results,
)
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

logger = logging.getLogger("test.perf")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

# Collect summaries across all tests in the module.
_module_summaries: list[BenchmarkSummary] = []


def _make_assembly_clips(clip_paths: list[Path], duration: float = 5.0):
    """Convert paths to AssemblyClip objects."""
    from immich_memories.processing.assembly_config import AssemblyClip

    return [
        AssemblyClip(path=p, duration=duration, asset_id=f"perf-{i}")
        for i, p in enumerate(clip_paths)
    ]


def _make_assembler(
    *,
    auto_resolution: bool,
    target_resolution: tuple[int, int],
) -> VideoAssembler:
    """Create an assembler with settings tuned for benchmarking."""
    return VideoAssembler(
        AssemblySettings(
            transition=TransitionType.CROSSFADE,
            transition_duration=0.3,
            output_crf=28,
            preserve_hdr=False,
            normalize_clip_audio=False,
            auto_resolution=auto_resolution,
            target_resolution=target_resolution,
        )
    )


def _run_assembly_benchmark(
    *,
    scenario: str,
    clip_paths: list[Path],
    clip_duration: float,
    resolution: str,
    target_resolution: tuple[int, int],
    tmp_path: Path,
    minimum_output_duration: float,
) -> BenchmarkSummary:
    clips = _make_assembly_clips(clip_paths, duration=clip_duration)

    def assemble_and_validate(output: Path) -> Path:
        assembler = _make_assembler(auto_resolution=False, target_resolution=target_resolution)
        assembler.assemble(clips, output)
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert get_duration(probe) > minimum_output_duration
        return output

    summary = run_repetitions(
        assemble_and_validate,
        scenario=scenario,
        output_dir=tmp_path / "benchmark-output",
        clip_count=len(clips),
        resolution=resolution,
        input_duration_seconds=clip_duration,
        codec="h264",
        frame_rate=30.0,
    )
    logger.info(summary.summary_line)
    _module_summaries.append(summary)
    return summary


class TestMinimalScenario:
    """2 clips, 720p, 5s each — baseline measurement."""

    def test_minimal_assembly_resources(self, test_clip_720p, test_clip_720p_b, tmp_path):
        summary = _run_assembly_benchmark(
            scenario="minimal",
            clip_paths=[test_clip_720p, test_clip_720p_b],
            clip_duration=3.0,
            resolution="1280x720",
            target_resolution=(1280, 720),
            tmp_path=tmp_path,
            minimum_output_duration=3.0,
        )
        assert len(summary.repetitions) == 3


class TestTypicalScenario:
    """5 clips, 1080p, 5s each — realistic self-hoster workload."""

    def test_typical_assembly_resources(self, fixtures_dir, tmp_path):
        clip_paths = make_n_clips(
            fixtures_dir,
            5,
            "1920x1080",
            duration=5,
            fps=30,
            codec="h264",
        )
        summary = _run_assembly_benchmark(
            scenario="typical",
            clip_paths=clip_paths,
            clip_duration=5.0,
            resolution="1920x1080",
            target_resolution=(1920, 1080),
            tmp_path=tmp_path,
            minimum_output_duration=10.0,
        )
        assert summary.median_wall_seconds > 0


class TestHeavyScenario:
    """8 clips, 1080p, 10s each — stress test (triggers chunking in current engine)."""

    def test_heavy_assembly_resources(self, fixtures_dir, tmp_path):
        clip_paths = make_n_clips(
            fixtures_dir,
            8,
            "1920x1080",
            duration=10,
            fps=30,
            codec="h264",
        )
        summary = _run_assembly_benchmark(
            scenario="heavy",
            clip_paths=clip_paths,
            clip_duration=10.0,
            resolution="1920x1080",
            target_resolution=(1920, 1080),
            tmp_path=tmp_path,
            minimum_output_duration=30.0,
        )
        assert summary.median_wall_seconds > 0


def test_save_results(tmp_path):
    """Save collected results to JSON after all scenarios complete."""
    if not _module_summaries:
        pytest.skip("No perf results collected")

    # Log human-readable summary
    logger.info("=" * 60)
    logger.info("PERFORMANCE SUMMARY")
    logger.info("=" * 60)
    for summary in _module_summaries:
        logger.info(summary.summary_line)
    logger.info("=" * 60)

    output = tmp_path / "perf-results.json"
    save_summary_results(_module_summaries, output)
    logger.info(f"Results saved to {output}")

    # Save benchmark JSON for github-action-benchmark
    bench_output = (
        Path(os.environ.get("BENCHMARK_OUTPUT_DIR", tmp_path)) / "benchmark-assembly.json"
    )
    save_benchmark_json(_module_summaries, bench_output)
    logger.info(f"Benchmark JSON saved to {bench_output}")
