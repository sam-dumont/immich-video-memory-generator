"""Performance measurement utilities for assembly benchmarks.

Uses stdlib only: tracemalloc, resource, time, subprocess.
No external dependencies.
"""

from __future__ import annotations

import json
import platform
import resource
import statistics
import subprocess
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REQUIRED_REPRODUCTION_KEYS = {
    "input_duration_seconds",
    "codec",
    "frame_rate",
    "cache_mode",
    "python_version",
    "platform",
    "cpu",
    "git_revision",
}


def _cpu_name() -> str:
    """Return the most specific stdlib-only CPU identity available."""
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    return platform.processor() or platform.machine() or "unknown"


def _git_revision() -> str:
    """Capture the exact checked-out revision without depending on GitPython."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


@dataclass
class PerfResult:
    """Performance measurement result for a single benchmark run."""

    scenario: str
    python_peak_mb: float
    wall_seconds: float
    cpu_user_seconds: float
    cpu_sys_seconds: float
    # WHY: FFmpeg runs as a subprocess — tracemalloc can't see it.
    # ru_maxrss from RUSAGE_CHILDREN captures child process peak RSS.
    child_peak_rss_mb: float = 0.0
    output_size_mb: float = 0.0
    clip_count: int = 0
    resolution: str = ""
    input_duration_seconds: float = 0.0
    codec: str = "unknown"
    frame_rate: float | str = 0.0
    cache_mode: str = "unknown"
    python_version: str = ""
    platform: str = ""
    cpu: str = ""
    git_revision: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready result including its reproduction identity."""
        return asdict(self)

    @property
    def summary_line(self) -> str:
        return (
            f"PERF: scenario={self.scenario} "
            f"python_peak_mb={self.python_peak_mb:.0f} "
            f"child_peak_rss_mb={self.child_peak_rss_mb:.0f} "
            f"wall_s={self.wall_seconds:.1f} "
            f"cpu_user_s={self.cpu_user_seconds:.1f} "
            f"cpu_sys_s={self.cpu_sys_seconds:.1f}"
        )


@dataclass(frozen=True)
class BenchmarkSummary:
    """One priming run plus the raw observations used for a median."""

    warmup: PerfResult
    repetitions: tuple[PerfResult, ...]

    def __post_init__(self) -> None:
        if len(self.repetitions) < 3:
            raise ValueError("A benchmark summary requires at least three measured repetitions")
        if any(result.scenario != self.warmup.scenario for result in self.repetitions):
            raise ValueError("Warmup and repetitions must describe the same scenario")
        if self.warmup.cache_mode != "cold":
            raise ValueError("Benchmark warmup must be labeled cold")
        if any(result.cache_mode != "warm" for result in self.repetitions):
            raise ValueError("Benchmark repetitions must be labeled warm")

        all_results = (self.warmup, *self.repetitions)
        for result in all_results:
            missing = [
                field
                for field in REQUIRED_REPRODUCTION_KEYS - {"cache_mode"}
                if getattr(result, field) in {None, "", "unknown", 0}
            ]
            if result.clip_count < 1 or not result.resolution:
                missing.extend(("clip_count", "resolution"))
            if missing:
                fields = ", ".join(sorted(set(missing)))
                raise ValueError(f"Benchmark reproduction field is missing: {fields}")

        identity_fields = (
            "scenario",
            "clip_count",
            "resolution",
            "input_duration_seconds",
            "codec",
            "frame_rate",
            "python_version",
            "platform",
            "cpu",
            "git_revision",
        )
        expected = tuple(getattr(self.warmup, field) for field in identity_fields)
        if any(
            tuple(getattr(result, field) for field in identity_fields) != expected
            for result in self.repetitions
        ):
            raise ValueError("Benchmark repetitions must share one reproduction identity")

    @property
    def scenario(self) -> str:
        return self.warmup.scenario

    @property
    def median_wall_seconds(self) -> float:
        return statistics.median(result.wall_seconds for result in self.repetitions)

    @property
    def median_peak_mb(self) -> float:
        peaks = [
            result.child_peak_rss_mb if result.child_peak_rss_mb > 0 else result.python_peak_mb
            for result in self.repetitions
        ]
        return statistics.median(peaks)

    @property
    def summary_line(self) -> str:
        raw = ",".join(f"{result.wall_seconds:.3f}" for result in self.repetitions)
        return (
            f"PERF SUMMARY: scenario={self.scenario} "
            f"warmup_s={self.warmup.wall_seconds:.3f} "
            f"repetitions_s=[{raw}] median_s={self.median_wall_seconds:.3f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "warmup": self.warmup.to_dict(),
            "repetitions": [result.to_dict() for result in self.repetitions],
            "median_wall_seconds": self.median_wall_seconds,
            "median_peak_mb": self.median_peak_mb,
        }


@contextmanager
def measure_resources(
    scenario: str,
    clip_count: int = 0,
    resolution: str = "",
    *,
    input_duration_seconds: float = 0.0,
    codec: str = "unknown",
    frame_rate: float | str = 0.0,
    cache_mode: str = "unknown",
) -> Generator[PerfResult, None, None]:
    """Context manager that measures Python peak memory, wall time, and CPU time.

    Usage:
        with measure_resources("typical", clip_count=5) as result:
            assembler.assemble(clips, output)
        print(result.summary_line)

    Python peak memory is measured via tracemalloc (tracks Python allocations).
    CPU time is measured via resource.getrusage (includes child processes on macOS).
    """
    result = PerfResult(
        scenario=scenario,
        python_peak_mb=0.0,
        wall_seconds=0.0,
        cpu_user_seconds=0.0,
        cpu_sys_seconds=0.0,
        clip_count=clip_count,
        resolution=resolution,
        input_duration_seconds=input_duration_seconds,
        codec=codec,
        frame_rate=frame_rate,
        cache_mode=cache_mode,
        python_version=platform.python_version(),
        platform=platform.platform(),
        cpu=_cpu_name(),
        git_revision=_git_revision(),
    )
    tracemalloc.start()
    start_wall = time.monotonic()
    start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)

    try:
        yield result
    finally:
        end_wall = time.monotonic()
        end_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        result.python_peak_mb = peak_bytes / (1024 * 1024)
        result.wall_seconds = end_wall - start_wall
        result.cpu_user_seconds = end_usage.ru_utime - start_usage.ru_utime
        result.cpu_sys_seconds = end_usage.ru_stime - start_usage.ru_stime

        # WHY: ru_maxrss is the HIGH WATERMARK of child process RSS.
        # On macOS it's in bytes, on Linux in KB. This captures FFmpeg's
        # memory usage which tracemalloc can't see.
        rss_raw = end_usage.ru_maxrss
        if platform.system() == "Darwin":
            result.child_peak_rss_mb = rss_raw / (1024 * 1024)
        else:
            result.child_peak_rss_mb = rss_raw / 1024


def _require_temporary_root(path: Path) -> Path:
    """Reject benchmark outputs outside recognized operating-system temp roots."""
    resolved = path.resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve()}
    temp_roots.update(
        candidate.resolve() for candidate in (Path("/tmp"), Path("/var/tmp")) if candidate.is_dir()
    )
    if not any(resolved.is_relative_to(root) for root in temp_roots):
        choices = ", ".join(str(root) for root in sorted(temp_roots))
        raise ValueError(f"Benchmark output must stay under a temporary root: {choices}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def run_repetitions(
    operation: Callable[[Path], Path | None],
    *,
    scenario: str,
    output_dir: Path,
    clip_count: int,
    resolution: str,
    input_duration_seconds: float,
    codec: str,
    frame_rate: float | str,
    repetitions: int = 3,
    suffix: str = ".mp4",
) -> BenchmarkSummary:
    """Run one cold priming pass and at least three warm measured passes."""
    if repetitions < 3:
        raise ValueError("Performance benchmarks require at least three repetitions")
    root = _require_temporary_root(output_dir)

    def run_once(label: str, cache_mode: str) -> PerfResult:
        output_path = root / f"{scenario}_{label}{suffix}"
        output_path.unlink(missing_ok=True)
        with measure_resources(
            scenario,
            clip_count=clip_count,
            resolution=resolution,
            input_duration_seconds=input_duration_seconds,
            codec=codec,
            frame_rate=frame_rate,
            cache_mode=cache_mode,
        ) as result:
            returned_path = operation(output_path)
        artifact = returned_path or output_path
        if artifact.is_file():
            result.output_size_mb = artifact.stat().st_size / (1024 * 1024)
        return result

    warmup = run_once("warmup", "cold")
    measured = tuple(run_once(f"rep_{index}", "warm") for index in range(1, repetitions + 1))
    return BenchmarkSummary(warmup=warmup, repetitions=measured)


def save_results(results: list[PerfResult], output_path: Path) -> None:
    """Save benchmark results to JSON for regression tracking."""
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "results": [asdict(r) for r in results],
    }
    output_path.write_text(json.dumps(data, indent=2) + "\n")


def save_summary_results(summaries: Sequence[BenchmarkSummary], output_path: Path) -> None:
    """Save raw repetitions and medians below an explicit temporary root."""
    _require_temporary_root(output_path.parent)
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": _cpu_name(),
        "git_revision": _git_revision(),
        "results": [summary.to_dict() for summary in summaries],
    }
    output_path.write_text(json.dumps(data, indent=2) + "\n")


def load_results(path: Path) -> list[PerfResult]:
    """Load previous benchmark results for comparison."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [PerfResult(**r) for r in data.get("results", [])]


def save_benchmark_json(
    results: Sequence[PerfResult | BenchmarkSummary], output_path: Path
) -> None:
    """Export results in github-action-benchmark's customSmallerIsBetter format.

    Produces a JSON list where each entry has name, unit, value.
    Wall time is the primary metric; peak memory (child RSS) is included
    as a secondary metric with a `:peak-memory` suffix.
    """
    entries: list[dict[str, str | float]] = []
    for result in results:
        if isinstance(result, BenchmarkSummary):
            wall_seconds = result.median_wall_seconds
            peak_mb = result.median_peak_mb
        else:
            wall_seconds = result.wall_seconds
            peak_mb = (
                result.child_peak_rss_mb if result.child_peak_rss_mb > 0 else result.python_peak_mb
            )
        entries.append(
            {"name": result.scenario, "unit": "seconds", "value": round(wall_seconds, 2)}
        )
        # WHY: child_peak_rss_mb captures FFmpeg subprocess memory, which is
        # the dominant allocation — more useful than Python heap for regression tracking.
        entries.append(
            {"name": f"{result.scenario}:peak-memory", "unit": "MB", "value": round(peak_mb, 1)}
        )
    output_path.write_text(json.dumps(entries, indent=2) + "\n")
