"""Contracts for reproducible local performance benchmarks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tests.integration.assembly import perf_utils
from tests.integration.assembly.conftest import make_n_clips
from tests.integration.assembly.perf_utils import PerfResult
from tests.integration.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

REQUIRED_REPRODUCTION_KEYS = {
    "input_duration_seconds",
    "codec",
    "frame_rate",
    "cache_mode",
    "python_version",
    "platform",
    "cpu",
    "git_revision",
    "warmup_wall_seconds",
    "raw_repetition_seconds",
    "median_wall_seconds",
}


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout)


def test_media_fixture_key_changes_with_duration(tmp_path: Path) -> None:
    five = make_n_clips(tmp_path, 1, "320x180", duration=5, fps=30, codec="h264")
    ten = make_n_clips(tmp_path, 1, "320x180", duration=10, fps=30, codec="h264")

    assert five[0] != ten[0]
    assert _probe_duration(five[0]) == pytest.approx(5, abs=0.2)
    assert _probe_duration(ten[0]) == pytest.approx(10, abs=0.2)


def test_perf_result_records_reproduction_inputs() -> None:
    payload = PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        wall_seconds=2.0,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
        python_version="3.13.5",
        platform="darwin-arm64",
        cpu="test-cpu",
        git_revision="abc1234",
    ).to_dict()

    assert payload.keys() >= REQUIRED_REPRODUCTION_KEYS


def test_repetition_summary_keeps_warmup_raw_measurements_and_median() -> None:
    calls: list[int] = []

    assert hasattr(perf_utils, "measure_repetitions")
    result = perf_utils.measure_repetitions(
        scenario="contract",
        operation=lambda index: calls.append(index),
        clip_count=1,
        resolution="640x360",
        input_duration_seconds=5.0,
        codec="h264",
        frame_rate=30.0,
        cache_mode="cold",
    )

    assert calls == [0, 1, 2, 3]
    assert result.warmup_wall_seconds is not None
    assert len(result.raw_repetition_seconds) == 3
    assert len(result.raw_repetition_metrics) == 3
    assert result.median_wall_seconds == pytest.approx(sorted(result.raw_repetition_seconds)[1])
    assert result.cpu_user_seconds == pytest.approx(
        sorted(sample["cpu_user_seconds"] for sample in result.raw_repetition_metrics)[1]
    )


def test_repeated_benchmarks_do_not_export_child_rss_as_comparable_peak(tmp_path: Path) -> None:
    result = PerfResult(
        scenario="contract",
        python_peak_mb=1.0,
        child_peak_rss_mb=42.0,
        wall_seconds=2.0,
        cpu_user_seconds=1.0,
        cpu_sys_seconds=0.2,
        raw_repetition_seconds=[1.9, 2.0, 2.1],
        median_wall_seconds=2.0,
    )
    output_path = tmp_path / "benchmark.json"

    perf_utils.save_benchmark_json([result], output_path)

    entries = json.loads(output_path.read_text())
    assert entries == [{"name": "contract", "unit": "seconds", "value": 2.0}]
