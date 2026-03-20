"""Performance measurement utilities for assembly benchmarks.

Uses stdlib only: tracemalloc, resource, time, subprocess.
No external dependencies.
"""

from __future__ import annotations

import json
import platform
import resource
import time
import tracemalloc
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


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
    scenario: str, clip_count: int = 0, resolution: str = ""
) -> Generator[PerfResult, None, None]:
    """Context manager that measures Python peak memory, wall time, and CPU time.

    Usage:
        with measure_resources("typical", clip_count=5) as result:
            assembler.assemble(clips, output)
        print(result.summary_line)

    Python peak memory is measured via tracemalloc (tracks Python allocations).
    CPU time is measured via resource.getrusage (includes child processes on macOS).
    """
    tracemalloc.start()
    start_wall = time.monotonic()
    start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)

    result = PerfResult(
        scenario=scenario,
        python_peak_mb=0.0,
        wall_seconds=0.0,
        cpu_user_seconds=0.0,
        cpu_sys_seconds=0.0,
        clip_count=clip_count,
        resolution=resolution,
    )

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


def save_results(results: list[PerfResult], output_path: Path) -> None:
    """Save benchmark results to JSON for regression tracking."""
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "results": [asdict(r) for r in results],
    }
    output_path.write_text(json.dumps(data, indent=2) + "\n")


def load_results(path: Path) -> list[PerfResult]:
    """Load previous benchmark results for comparison."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [PerfResult(**r) for r in data.get("results", [])]
