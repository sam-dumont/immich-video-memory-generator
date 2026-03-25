"""Performance benchmarks for video assembly.

Measures Python peak memory, wall time, and CPU time for realistic
assembly scenarios. Run with: make benchmark-perf

Results are logged as structured PERF: lines and optionally saved
to tests/perf-results.json for regression tracking.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.integration.assembly.conftest import make_n_clips
from tests.integration.assembly.perf_utils import (
    PerfResult,
    measure_resources,
    save_benchmark_json,
    save_results,
)
from tests.integration.conftest import (
    ffprobe_json,
    get_duration,
    has_stream,
    requires_ffmpeg,
)

logger = logging.getLogger("test.perf")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

# Collect results across all tests in the module
_module_results: list[PerfResult] = []


def _make_assembly_clips(clip_paths: list[Path], duration: float = 5.0):
    """Convert paths to AssemblyClip objects."""
    from immich_memories.processing.assembly_config import AssemblyClip

    return [
        AssemblyClip(path=p, duration=duration, asset_id=f"perf-{i}")
        for i, p in enumerate(clip_paths)
    ]


def _make_assembler(**overrides):
    """Create an assembler with settings tuned for benchmarking."""
    from immich_memories.processing.assembly_config import AssemblySettings, TransitionType
    from immich_memories.processing.video_assembler import VideoAssembler

    defaults = {
        "transition": TransitionType.CROSSFADE,
        "transition_duration": 0.3,
        "output_crf": 28,
        "preserve_hdr": False,
        "normalize_clip_audio": False,
    }
    defaults.update(overrides)
    return VideoAssembler(AssemblySettings(**defaults))


class TestMinimalScenario:
    """2 clips, 720p, 5s each — baseline measurement."""

    def test_minimal_assembly_resources(self, test_clip_720p, test_clip_720p_b, tmp_path):
        output = tmp_path / "perf_minimal.mp4"
        assembler = _make_assembler(auto_resolution=False, target_resolution=(1280, 720))
        clips = _make_assembly_clips([test_clip_720p, test_clip_720p_b], duration=3.0)

        with measure_resources("minimal", clip_count=2, resolution="720p") as result:
            assembler.assemble(clips, output)

        result.output_size_mb = output.stat().st_size / (1024 * 1024)

        # Verify output is valid
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 3.0

        logger.info(result.summary_line)
        _module_results.append(result)


class TestTypicalScenario:
    """5 clips, 1080p, 5s each — realistic self-hoster workload."""

    def test_typical_assembly_resources(self, fixtures_dir, tmp_path):
        clip_paths = make_n_clips(fixtures_dir, 5, "1920x1080", duration=5)
        output = tmp_path / "perf_typical.mp4"
        assembler = _make_assembler(auto_resolution=False, target_resolution=(1920, 1080))
        clips = _make_assembly_clips(clip_paths, duration=5.0)

        with measure_resources("typical", clip_count=5, resolution="1080p") as result:
            assembler.assemble(clips, output)

        result.output_size_mb = output.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 10.0

        logger.info(result.summary_line)
        _module_results.append(result)


class TestHeavyScenario:
    """8 clips, 1080p, 10s each — stress test (triggers chunking in current engine)."""

    def test_heavy_assembly_resources(self, fixtures_dir, tmp_path):
        clip_paths = make_n_clips(fixtures_dir, 8, "1920x1080", duration=10)
        output = tmp_path / "perf_heavy.mp4"
        assembler = _make_assembler(auto_resolution=False, target_resolution=(1920, 1080))
        clips = _make_assembly_clips(clip_paths, duration=10.0)

        with measure_resources("heavy", clip_count=8, resolution="1080p") as result:
            assembler.assemble(clips, output)

        result.output_size_mb = output.stat().st_size / (1024 * 1024)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 30.0

        logger.info(result.summary_line)
        _module_results.append(result)


def test_save_results(tmp_path):
    """Save collected results to JSON after all scenarios complete."""
    if not _module_results:
        pytest.skip("No perf results collected")

    # Log human-readable summary
    logger.info("=" * 60)
    logger.info("PERFORMANCE SUMMARY")
    logger.info("=" * 60)
    for r in _module_results:
        logger.info(r.summary_line)
    logger.info("=" * 60)

    # Save to project root for tracking
    project_root = Path(__file__).resolve().parents[3]
    output = project_root / "tests" / "perf-results.json"
    save_results(_module_results, output)
    logger.info(f"Results saved to {output}")

    # Save benchmark JSON for github-action-benchmark
    bench_output = project_root / "tests" / "benchmark-assembly.json"
    save_benchmark_json(_module_results, bench_output)
    logger.info(f"Benchmark JSON saved to {bench_output}")
