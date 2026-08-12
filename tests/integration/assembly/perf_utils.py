"""Performance measurement utilities for assembly benchmarks.

Uses stdlib only: tracemalloc, resource, time, subprocess.
No external dependencies.
"""

from __future__ import annotations

import json
import platform
import resource
import subprocess
import time
import tracemalloc
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median

REQUIRED_REPRODUCTION_KEYS = frozenset(
    {
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
)


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
    codec: str = ""
    frame_rate: float = 0.0
    cache_mode: str = ""
    python_version: str = ""
    platform: str = ""
    cpu: str = ""
    git_revision: str = ""
    warmup_wall_seconds: float | None = None
    raw_repetition_seconds: list[float] = field(default_factory=list)
    raw_repetition_metrics: list[dict[str, float]] = field(default_factory=list)
    median_wall_seconds: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the benchmark and its reproduction inputs."""
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


@contextmanager
def measure_resources(
    scenario: str,
    clip_count: int = 0,
    resolution: str = "",
    input_duration_seconds: float = 0.0,
    codec: str = "",
    frame_rate: float = 0.0,
    cache_mode: str = "",
    metadata_collector: Callable[[], dict[str, str]] | None = None,
) -> Generator[PerfResult, None, None]:
    """Context manager that measures Python peak memory, wall time, and CPU time.

    Usage:
        with measure_resources("typical", clip_count=5) as result:
            assembler.assemble(clips, output)
        print(result.summary_line)

    Python peak memory is measured via tracemalloc (tracks Python allocations).
    CPU time is measured via resource.getrusage (includes child processes on macOS).
    """
    metadata = (metadata_collector or _collect_reproduction_metadata)()

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
        python_version=metadata["python_version"],
        platform=metadata["platform"],
        cpu=metadata["cpu"],
        git_revision=metadata["git_revision"],
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


def _git_revision() -> str:
    """Return the checked-out revision without making benchmarks depend on Git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _command_output(command: list[str]) -> str | None:
    """Return stripped stdout from a small identity probe, if available."""
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _cpu_fingerprint() -> str:
    """Return a useful, portable CPU identity for benchmark reproduction."""
    system = platform.system()
    if system == "Darwin":
        hardware = _command_output(["system_profiler", "SPHardwareDataType"])
        if hardware:
            for line in hardware.splitlines():
                label, separator, value = line.strip().partition(":")
                if separator and label in {"Chip", "Processor Name"} and value.strip():
                    return value.strip()
        sysctl_brand = _command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
        if sysctl_brand:
            return sysctl_brand
    elif system == "Linux":
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text()
        except OSError:
            cpuinfo = ""
        for label in ("model name", "hardware", "processor"):
            for line in cpuinfo.splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() == label and value.strip():
                    return value.strip()

    return platform.processor() or platform.machine() or "unknown"


def _collect_reproduction_metadata() -> dict[str, str]:
    """Collect benchmark identity before any resource snapshot starts."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu": _cpu_fingerprint(),
        "git_revision": _git_revision(),
    }


def measure_repetitions(
    *,
    scenario: str,
    operation: Callable[[int], None],
    clip_count: int,
    resolution: str,
    input_duration_seconds: float,
    codec: str,
    frame_rate: float,
    cache_mode: str,
) -> PerfResult:
    """Run one warm-up plus three measurements and return the median summary."""
    with measure_resources(
        scenario,
        clip_count=clip_count,
        resolution=resolution,
        input_duration_seconds=input_duration_seconds,
        codec=codec,
        frame_rate=frame_rate,
        cache_mode=cache_mode,
    ) as warmup:
        operation(0)

    measurements: list[PerfResult] = []
    for index in range(1, 4):
        with measure_resources(
            scenario,
            clip_count=clip_count,
            resolution=resolution,
            input_duration_seconds=input_duration_seconds,
            codec=codec,
            frame_rate=frame_rate,
            cache_mode=cache_mode,
        ) as result:
            operation(index)
        measurements.append(result)

    summary = measurements[-1]
    summary.warmup_wall_seconds = warmup.wall_seconds
    summary.raw_repetition_seconds = [result.wall_seconds for result in measurements]
    summary.raw_repetition_metrics = [
        {
            "python_peak_mb": result.python_peak_mb,
            "child_peak_rss_mb": result.child_peak_rss_mb,
            "wall_seconds": result.wall_seconds,
            "cpu_user_seconds": result.cpu_user_seconds,
            "cpu_sys_seconds": result.cpu_sys_seconds,
        }
        for result in measurements
    ]
    summary.median_wall_seconds = median(summary.raw_repetition_seconds)
    summary.wall_seconds = summary.median_wall_seconds
    summary.python_peak_mb = median(result.python_peak_mb for result in measurements)
    summary.cpu_user_seconds = median(result.cpu_user_seconds for result in measurements)
    summary.cpu_sys_seconds = median(result.cpu_sys_seconds for result in measurements)
    return summary


def save_results(results: list[PerfResult], output_path: Path) -> None:
    """Save benchmark results to JSON for regression tracking."""
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "results": [r.to_dict() for r in results],
        "summary": [
            {
                "scenario": result.scenario,
                "median_wall_seconds": result.median_wall_seconds or result.wall_seconds,
                "raw_repetition_seconds": result.raw_repetition_seconds,
            }
            for result in results
        ],
    }
    output_path.write_text(json.dumps(data, indent=2) + "\n")


def load_results(path: Path) -> list[PerfResult]:
    """Load previous benchmark results for comparison."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [PerfResult(**r) for r in data.get("results", [])]


def save_benchmark_json(results: list[PerfResult], output_path: Path) -> None:
    """Export results in github-action-benchmark's customSmallerIsBetter format.

    Produces a JSON list where each entry has name, unit, value.
    Wall time is the primary metric. Legacy single-run benchmarks also export
    peak memory; repeated runs omit it because child RSS is a process-lifetime
    high-water mark, not a comparable per-repetition measurement.
    """
    entries: list[dict[str, str | float]] = []
    for r in results:
        entries.append({"name": r.scenario, "unit": "seconds", "value": round(r.wall_seconds, 2)})
        if not r.raw_repetition_seconds:
            # WHY: child_peak_rss_mb captures FFmpeg subprocess memory, which is
            # the dominant allocation — more useful than Python heap for legacy
            # single-run regression tracking.
            peak_mb = r.child_peak_rss_mb if r.child_peak_rss_mb > 0 else r.python_peak_mb
            entries.append(
                {"name": f"{r.scenario}:peak-memory", "unit": "MB", "value": round(peak_mb, 1)}
            )
    output_path.write_text(json.dumps(entries, indent=2) + "\n")
