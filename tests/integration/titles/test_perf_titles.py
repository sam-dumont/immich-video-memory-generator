"""Performance benchmarks for title screen rendering.

Measures wall time and memory for different title screen types.
Run with: make benchmark-titles

Results saved under the temporary benchmark output root with raw repetitions and action metrics.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.integration.assembly.perf_utils import (
    BenchmarkSummary,
    run_repetitions,
    save_benchmark_json,
)
from tests.integration.conftest import requires_ffmpeg

logger = logging.getLogger("test.perf.titles")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

_module_results: list[BenchmarkSummary] = []


def _run_title_benchmark(
    *, scenario: str, tmp_path: Path, duration: float, render: Callable[[Path], None]
) -> BenchmarkSummary:
    """Measure one title scenario with a cold warmup and three warm observations."""

    def render_and_require_output(output: Path) -> Path:
        render(output)
        assert output.is_file(), f"Title benchmark renderer did not create {output.name}"
        return output

    summary = run_repetitions(
        render_and_require_output,
        scenario=scenario,
        output_dir=tmp_path / "benchmark-output",
        clip_count=1,
        resolution="1280x720",
        input_duration_seconds=duration,
        codec="h264",
        frame_rate=30.0,
    )
    logger.info(summary.summary_line)
    _module_results.append(summary)
    return summary


def test_title_benchmark_rejects_a_renderer_that_does_not_create_output(tmp_path: Path) -> None:
    """A missing render artifact must not become a meaningless timing summary."""
    with pytest.raises(AssertionError, match="did not create"):
        _run_title_benchmark(
            scenario="missing-output",
            tmp_path=tmp_path,
            duration=1.0,
            render=lambda _output: None,
        )


class TestTitleScreenPerf:
    """Benchmark title screen generation via convenience API."""

    def test_gradient_720p(self, tmp_path: Path) -> None:
        from immich_memories.titles.convenience import generate_title_screen
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(
            name="bench-gradient",
            background_type="soft_gradient",
            background_colors=["#1a1a2e", "#16213e"],
        )

        def render(output: Path) -> None:
            generate_title_screen(
                title="2024",
                subtitle="Family Memories",
                style=style,
                output_path=output,
                resolution="720p",
                duration=3.5,
                fps=30.0,
                animated_background=True,
            )

        summary = _run_title_benchmark(
            scenario="title-gradient-720p", tmp_path=tmp_path, duration=3.5, render=render
        )
        assert len(summary.repetitions) == 3

    def test_content_backed_720p(self, tmp_path: Path) -> None:
        from immich_memories.titles.convenience import generate_title_screen
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(
            name="bench-content-backed",
            background_type="content_backed",
            background_colors=["#0a0a0a", "#1a1a1a"],
        )

        def render(output: Path) -> None:
            generate_title_screen(
                title="Summer Trip",
                subtitle="July 2024",
                style=style,
                output_path=output,
                resolution="720p",
                duration=3.5,
                fps=30.0,
                animated_background=True,
            )

        summary = _run_title_benchmark(
            scenario="title-content-backed-720p", tmp_path=tmp_path, duration=3.5, render=render
        )
        assert len(summary.repetitions) == 3


class TestEndingScreenPerf:
    """Benchmark ending screen generation."""

    def test_ending_720p(self, tmp_path: Path) -> None:
        from immich_memories.titles.convenience import generate_ending_screen
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(
            name="bench-ending",
            background_colors=["#1a1a2e", "#16213e"],
        )

        def render(output: Path) -> None:
            generate_ending_screen(
                style=style,
                output_path=output,
                resolution="720p",
                duration=4.0,
                fps=30.0,
            )

        summary = _run_title_benchmark(
            scenario="title-ending-720p", tmp_path=tmp_path, duration=4.0, render=render
        )
        assert len(summary.repetitions) == 3


def test_save_title_benchmarks() -> None:
    """Save collected title benchmark results."""
    if not _module_results:
        pytest.skip("No title perf results collected")

    logger.info("=" * 60)
    logger.info("TITLE PERFORMANCE SUMMARY")
    logger.info("=" * 60)
    for summary in _module_results:
        logger.info(summary.summary_line)
    logger.info("=" * 60)

    output = (
        Path(os.environ.get("BENCHMARK_OUTPUT_DIR", tempfile.gettempdir()))
        / "benchmark-titles.json"
    )
    save_benchmark_json(_module_results, output)
    logger.info(f"Benchmark JSON saved to {output}")
