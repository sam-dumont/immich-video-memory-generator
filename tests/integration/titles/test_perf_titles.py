"""Performance benchmarks for title screen rendering.

Measures wall time and memory for different title screen types.
Run with: make benchmark-titles

Results saved to tests/benchmark-titles.json in customSmallerIsBetter format.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.integration.assembly.perf_utils import PerfResult, measure_resources, save_benchmark_json
from tests.integration.conftest import requires_ffmpeg

logger = logging.getLogger("test.perf.titles")

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.perf]

_module_results: list[PerfResult] = []


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
        output = tmp_path / "gradient_720p.mp4"

        with measure_resources("title-gradient-720p") as result:
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

        assert output.exists()
        result.output_size_mb = output.stat().st_size / (1024 * 1024)
        logger.info(result.summary_line)
        _module_results.append(result)

    def test_content_backed_720p(self, tmp_path: Path) -> None:
        from immich_memories.titles.convenience import generate_title_screen
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(
            name="bench-content-backed",
            background_type="content_backed",
            background_colors=["#0a0a0a", "#1a1a1a"],
        )
        output = tmp_path / "content_backed_720p.mp4"

        with measure_resources("title-content-backed-720p") as result:
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

        assert output.exists()
        result.output_size_mb = output.stat().st_size / (1024 * 1024)
        logger.info(result.summary_line)
        _module_results.append(result)


class TestEndingScreenPerf:
    """Benchmark ending screen generation."""

    def test_ending_720p(self, tmp_path: Path) -> None:
        from immich_memories.titles.convenience import generate_ending_screen
        from immich_memories.titles.styles import TitleStyle

        style = TitleStyle(
            name="bench-ending",
            background_colors=["#1a1a2e", "#16213e"],
        )
        output = tmp_path / "ending_720p.mp4"

        with measure_resources("title-ending-720p") as result:
            generate_ending_screen(
                style=style,
                output_path=output,
                resolution="720p",
                duration=4.0,
                fps=30.0,
            )

        assert output.exists()
        result.output_size_mb = output.stat().st_size / (1024 * 1024)
        logger.info(result.summary_line)
        _module_results.append(result)


def test_save_title_benchmarks() -> None:
    """Save collected title benchmark results."""
    if not _module_results:
        pytest.skip("No title perf results collected")

    logger.info("=" * 60)
    logger.info("TITLE PERFORMANCE SUMMARY")
    logger.info("=" * 60)
    for r in _module_results:
        logger.info(r.summary_line)
    logger.info("=" * 60)

    project_root = Path(__file__).resolve().parents[3]
    output = project_root / "tests" / "benchmark-titles.json"
    save_benchmark_json(_module_results, output)
    logger.info(f"Benchmark JSON saved to {output}")
