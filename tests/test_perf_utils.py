"""Unit tests for performance measurement utilities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.assembly.perf_utils import (
    BenchmarkSummary,
    PerfResult,
    measure_resources,
    save_benchmark_json,
)


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


class TestBenchmarkJsonExport:
    """Tests for customSmallerIsBetter JSON export used by github-action-benchmark."""

    def _sample_results(self) -> list[BenchmarkSummary]:
        def summary(
            *, scenario: str, wall_seconds: float, peak_mb: float, clip_count: int, resolution: str
        ) -> BenchmarkSummary:
            warmup = PerfResult(
                scenario=scenario,
                python_peak_mb=50.0,
                wall_seconds=wall_seconds + 1.0,
                cpu_user_seconds=3.0,
                cpu_sys_seconds=1.0,
                child_peak_rss_mb=peak_mb,
                clip_count=clip_count,
                resolution=resolution,
                input_duration_seconds=3.0,
                codec="h264",
                frame_rate=30.0,
                cache_mode="cold",
                python_version="3.12.11",
                platform="test-platform",
                cpu="test-cpu",
                git_revision="abc1234",
            )
            return BenchmarkSummary(
                warmup=warmup,
                repetitions=tuple(
                    PerfResult(
                        **{**warmup.to_dict(), "cache_mode": "warm", "wall_seconds": measurement}
                    )
                    for measurement in (wall_seconds - 0.2, wall_seconds, wall_seconds + 0.2)
                ),
            )

        return [
            summary(
                scenario="assembly-720p-2clips",
                wall_seconds=4.8,
                peak_mb=200.0,
                clip_count=2,
                resolution="720p",
            ),
            summary(
                scenario="assembly-1080p-5clips",
                wall_seconds=12.5,
                peak_mb=400.0,
                clip_count=5,
                resolution="1080p",
            ),
        ]

    def test_produces_valid_json_list(self, tmp_path: Path) -> None:
        """Output must be a JSON list of benchmark entries."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        assert isinstance(data, dict)
        # 2 results × 2 metrics (wall time + peak memory) = 4 entries
        assert len(data["benchmarks"]) == 4

    def test_entries_have_required_fields(self, tmp_path: Path) -> None:
        """Each entry must have name, unit, value per customSmallerIsBetter spec."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        for entry in data["benchmarks"]:
            assert "name" in entry
            assert "unit" in entry
            assert "value" in entry
            assert isinstance(entry["value"], (int, float))

    def test_wall_time_entries(self, tmp_path: Path) -> None:
        """Wall time entries should use scenario name and seconds unit."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        wall_entries = [e for e in data["benchmarks"] if e["unit"] == "seconds"]
        assert len(wall_entries) == 2
        assert wall_entries[0]["name"] == "assembly-720p-2clips"
        assert wall_entries[0]["value"] == 4.8

    def test_empty_results_produces_empty_list(self, tmp_path: Path) -> None:
        """Empty input should produce an empty JSON list."""
        output = tmp_path / "bench.json"
        save_benchmark_json([], output)

        data = json.loads(output.read_text())
        assert data["benchmarks"] == []
        assert data["results"] == []
        assert data["git_revision"]

    def test_extra_metrics_included(self, tmp_path: Path) -> None:
        """Peak memory should be included as a separate metric."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results()[:1], output)

        data = json.loads(output.read_text())
        names = {e["name"] for e in data["benchmarks"]}
        # Wall time + memory metric
        assert "assembly-720p-2clips" in names
        assert "assembly-720p-2clips:peak-memory" in names

    def test_memory_metric_uses_mb(self, tmp_path: Path) -> None:
        """Memory entries should use MB unit."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results()[:1], output)

        data = json.loads(output.read_text())
        mem_entries = [e for e in data["benchmarks"] if e["unit"] == "MB"]
        assert len(mem_entries) == 1
        assert mem_entries[0]["value"] == 200.0

    def test_summary_export_keeps_reproduction_runs_beside_action_metrics(
        self, tmp_path: Path
    ) -> None:
        """Dropping raw warmup/repetitions would make a submitted metric unreproducible."""
        result = PerfResult(
            scenario="assembly-720p-2clips",
            python_peak_mb=50.0,
            wall_seconds=4.8,
            cpu_user_seconds=3.0,
            cpu_sys_seconds=1.0,
            child_peak_rss_mb=200.0,
            clip_count=2,
            resolution="1280x720",
            input_duration_seconds=3.0,
            codec="h264",
            frame_rate=30.0,
            cache_mode="cold",
            python_version="3.12.11",
            platform="test-platform",
            cpu="test-cpu",
            git_revision="abc1234",
        )
        summary = BenchmarkSummary(
            warmup=result,
            repetitions=tuple(
                PerfResult(**{**result.to_dict(), "cache_mode": "warm", "wall_seconds": value})
                for value in (4.0, 4.8, 5.0)
            ),
        )
        output = tmp_path / "bench.json"

        save_benchmark_json([summary], output)

        data = json.loads(output.read_text())
        assert data["benchmarks"][0] == {
            "name": "assembly-720p-2clips",
            "unit": "seconds",
            "value": 4.8,
        }
        assert data["results"][0]["warmup"]["cache_mode"] == "cold"
        assert len(data["results"][0]["repetitions"]) == 3

    def test_legacy_metric_only_results_are_rejected_for_submission(self, tmp_path: Path) -> None:
        """Accepting a lone metric would bypass the workflow repetition gate."""
        legacy: list[PerfResult] = [
            PerfResult(
                scenario="legacy",
                python_peak_mb=1.0,
                wall_seconds=1.0,
                cpu_user_seconds=1.0,
                cpu_sys_seconds=0.0,
            )
        ]
        with pytest.raises(TypeError, match="BenchmarkSummary"):
            save_benchmark_json(legacy, tmp_path / "bench.json")  # type: ignore[arg-type]
