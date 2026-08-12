"""Unit tests for performance measurement utilities."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from tests.integration.assembly import perf_utils
from tests.integration.assembly.perf_utils import (
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

    def test_reproduction_metadata_collection_is_outside_every_measurement(self) -> None:
        """Slow metadata probes must not inflate wall, heap, or child CPU samples."""
        retained_allocations: list[bytearray] = []

        def collect_metadata() -> dict[str, str]:
            retained_allocations.append(bytearray(4_000_000))
            subprocess.run(
                [sys.executable, "-c", "sum(range(3_000_000))"],
                check=True,
                capture_output=True,
            )
            time.sleep(0.08)
            return {
                "python_version": "test-python",
                "platform": "test-platform",
                "cpu": "test-cpu",
                "git_revision": "test-revision",
            }

        outer_start = time.monotonic()
        with measure_resources("metadata", metadata_collector=collect_metadata) as result:
            pass
        outer_seconds = time.monotonic() - outer_start

        assert outer_seconds >= 0.08
        assert result.wall_seconds < 0.04
        assert result.python_peak_mb < 1.0
        assert result.cpu_user_seconds < 0.04
        assert result.cpu_sys_seconds < 0.04
        assert result.python_version == "test-python"
        assert result.git_revision == "test-revision"

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


class TestCpuFingerprint:
    def test_macos_uses_the_hardware_chip_name(self, monkeypatch) -> None:
        """Apple Silicon should be identified as the actual chip, not generic arm."""
        monkeypatch.setattr(perf_utils.platform, "system", lambda: "Darwin")

        def run(command, **_kwargs):
            assert command == ["system_profiler", "SPHardwareDataType"]
            return SimpleNamespace(stdout="Hardware:\n    Chip: Apple M5 Max\n")

        monkeypatch.setattr(perf_utils.subprocess, "run", run)

        assert perf_utils._cpu_fingerprint() == "Apple M5 Max"

    def test_macos_falls_back_when_system_profiler_is_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(perf_utils.platform, "system", lambda: "Darwin")

        def run(command, **_kwargs):
            if command[0] == "system_profiler":
                raise FileNotFoundError
            assert command == ["sysctl", "-n", "machdep.cpu.brand_string"]
            return SimpleNamespace(stdout="Apple M4 Pro\n")

        monkeypatch.setattr(perf_utils.subprocess, "run", run)

        assert perf_utils._cpu_fingerprint() == "Apple M4 Pro"

    def test_unknown_platform_falls_back_to_machine(self, monkeypatch) -> None:
        monkeypatch.setattr(perf_utils.platform, "system", lambda: "Plan9")
        monkeypatch.setattr(perf_utils.platform, "processor", lambda: "")
        monkeypatch.setattr(perf_utils.platform, "machine", lambda: "riscv64")

        assert perf_utils._cpu_fingerprint() == "riscv64"


class TestBenchmarkJsonExport:
    """Tests for customSmallerIsBetter JSON export used by github-action-benchmark."""

    def _sample_results(self) -> list[PerfResult]:
        return [
            PerfResult(
                scenario="assembly-720p-2clips",
                python_peak_mb=50.0,
                wall_seconds=4.8,
                cpu_user_seconds=3.0,
                cpu_sys_seconds=1.0,
                child_peak_rss_mb=200.0,
                clip_count=2,
                resolution="720p",
            ),
            PerfResult(
                scenario="assembly-1080p-5clips",
                python_peak_mb=120.0,
                wall_seconds=12.5,
                cpu_user_seconds=8.0,
                cpu_sys_seconds=2.0,
                child_peak_rss_mb=400.0,
                clip_count=5,
                resolution="1080p",
            ),
        ]

    def test_produces_valid_json_list(self, tmp_path: Path) -> None:
        """Output must be a JSON list of benchmark entries."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        assert isinstance(data, list)
        # 2 results × 2 metrics (wall time + peak memory) = 4 entries
        assert len(data) == 4

    def test_entries_have_required_fields(self, tmp_path: Path) -> None:
        """Each entry must have name, unit, value per customSmallerIsBetter spec."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        for entry in data:
            assert "name" in entry
            assert "unit" in entry
            assert "value" in entry
            assert isinstance(entry["value"], (int, float))

    def test_wall_time_entries(self, tmp_path: Path) -> None:
        """Wall time entries should use scenario name and seconds unit."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results(), output)

        data = json.loads(output.read_text())
        wall_entries = [e for e in data if e["unit"] == "seconds"]
        assert len(wall_entries) == 2
        assert wall_entries[0]["name"] == "assembly-720p-2clips"
        assert wall_entries[0]["value"] == 4.8

    def test_empty_results_produces_empty_list(self, tmp_path: Path) -> None:
        """Empty input should produce an empty JSON list."""
        output = tmp_path / "bench.json"
        save_benchmark_json([], output)

        data = json.loads(output.read_text())
        assert data == []

    def test_extra_metrics_included(self, tmp_path: Path) -> None:
        """Peak memory should be included as a separate metric."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results()[:1], output)

        data = json.loads(output.read_text())
        names = {e["name"] for e in data}
        # Wall time + memory metric
        assert "assembly-720p-2clips" in names
        assert "assembly-720p-2clips:peak-memory" in names

    def test_memory_metric_uses_mb(self, tmp_path: Path) -> None:
        """Memory entries should use MB unit."""
        output = tmp_path / "bench.json"
        save_benchmark_json(self._sample_results()[:1], output)

        data = json.loads(output.read_text())
        mem_entries = [e for e in data if e["unit"] == "MB"]
        assert len(mem_entries) == 1
        assert mem_entries[0]["value"] == 200.0
