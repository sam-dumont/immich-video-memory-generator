"""Unit tests for performance measurement utilities."""

from __future__ import annotations

from tests.integration.assembly.perf_utils import PerfResult, measure_resources


class TestMeasureResources:
    def test_captures_wall_time(self) -> None:
        """measure_resources should capture non-zero wall time."""
        import time

        with measure_resources("test_wall") as result:
            time.sleep(0.05)

        assert result.wall_seconds >= 0.04
        assert result.scenario == "test_wall"

    def test_captures_python_peak(self) -> None:
        """measure_resources should capture Python memory allocation."""
        with measure_resources("test_mem") as result:
            # Allocate ~1MB
            _ = bytearray(1_000_000)

        assert result.python_peak_mb >= 0.9

    def test_summary_line_format(self) -> None:
        """PerfResult.summary_line should produce parseable output."""
        r = PerfResult(
            scenario="test",
            python_peak_mb=100.5,
            wall_seconds=5.2,
            cpu_user_seconds=3.1,
            cpu_sys_seconds=1.0,
            child_peak_rss_mb=500.0,
        )
        line = r.summary_line
        assert "scenario=test" in line
        assert "python_peak_mb=100" in line
        assert "child_peak_rss_mb=500" in line
        assert "wall_s=5.2" in line
