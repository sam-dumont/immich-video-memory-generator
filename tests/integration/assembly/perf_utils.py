"""Performance measurement utilities for assembly benchmarks.

Uses stdlib only: tracemalloc, resource, time, subprocess.
No external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import resource
import stat
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


def require_benchmark_temporary_root(path: Path, *, create: bool = True) -> Path:
    """Return one canonical OS-temporary root without following caller-created symlinks."""
    absolute = path.absolute()
    aliases = [Path(tempfile.gettempdir()).absolute()]
    aliases.extend(
        candidate for candidate in (Path("/tmp"), Path("/var/tmp")) if candidate.is_dir()
    )
    roots = {alias.resolve() for alias in aliases}
    trusted_prefix = next(
        (alias for alias in aliases if absolute.is_relative_to(alias)),
        next((root for root in roots if absolute.is_relative_to(root)), None),
    )
    if trusted_prefix is None:
        choices = ", ".join(str(root) for root in sorted(roots))
        raise ValueError(f"Benchmark output must stay under a temporary root: {choices}")
    component = trusted_prefix
    for part in absolute.relative_to(trusted_prefix).parts:
        component /= part
        if component.is_symlink():
            raise ValueError(f"Benchmark temporary root has a symlink ancestor: {component}")
    resolved = absolute.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError("Benchmark temporary root resolves outside the OS temporary roots")
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    elif not resolved.is_dir():
        raise ValueError("Benchmark temporary root does not exist")
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
    root = require_benchmark_temporary_root(output_dir)

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
        if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size <= 0:
            raise ValueError("Benchmark operation must return a nonempty regular file")
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
    require_benchmark_temporary_root(output_path.parent)
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


def save_benchmark_json(results: Sequence[BenchmarkSummary], output_path: Path) -> None:
    """Export reproducible summaries with customSmallerIsBetter metrics beside them.

    The workflow validates ``results`` before projecting ``benchmarks`` into the list accepted by
    github-action-benchmark. A standalone ``PerfResult`` cannot prove a warmup or three samples.
    """
    entries: list[dict[str, str | float]] = []
    for result in results:
        if not isinstance(result, BenchmarkSummary):
            raise TypeError("Benchmark submission requires BenchmarkSummary values")
        wall_seconds = result.median_wall_seconds
        peak_mb = result.median_peak_mb
        entries.append(
            {"name": result.scenario, "unit": "seconds", "value": round(wall_seconds, 2)}
        )
        # WHY: child_peak_rss_mb captures FFmpeg subprocess memory, which is
        # the dominant allocation — more useful than Python heap for regression tracking.
        entries.append(
            {"name": f"{result.scenario}:peak-memory", "unit": "MB", "value": round(peak_mb, 1)}
        )
    require_benchmark_temporary_root(output_path.parent)
    # ``git_revision`` is deliberately the exact HEAD, not ``describe --dirty``:
    # the separate fingerprint identifies the worktree contents that produced it.
    revision = os.environ.get("BENCHMARK_REVISION") or _git_revision()
    fingerprint = _require_exact_source_fingerprint(
        os.environ.get("IMMICH_BENCHMARK_BATCH_FINGERPRINT") or current_source_fingerprint()
    )
    payload = {
        "benchmarks": entries,
        "git_revision": revision,
        "source_fingerprint": fingerprint,
        "results": [result.to_dict() for result in results],
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


class SourceFingerprintError(RuntimeError):
    """Benchmark source identity could not be computed without following unsafe state."""


def _require_exact_source_fingerprint(fingerprint: str) -> str:
    if not fingerprint or fingerprint == "unknown":
        raise SourceFingerprintError("Benchmark source identity is indeterminate")
    return fingerprint


def source_fingerprint() -> str:
    """Hash only benchmark-relevant tracked and untracked source state.

    User-owned scratch directories (notably ``MagicMock/``) must not change benchmark identity or
    be read while submitting an otherwise identical run.
    """
    digest = hashlib.sha256()
    relevant_paths = (
        "src",
        "tests/integration/assembly",
        "tests/integration/pipeline",
        "tests/integration/titles",
        "tests/integration/assembly/perf_utils.py",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
    )
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", *relevant_paths],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout.split(b"\0")
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise SourceFingerprintError(
            "Git could not compute the benchmark source identity"
        ) from error
    digest.update(diff)
    for raw_path in sorted(path for path in untracked if path):
        try:
            decoded = raw_path.decode()
        except UnicodeDecodeError as error:
            raise SourceFingerprintError("Untracked benchmark path is not valid UTF-8") from error
        if not any(decoded == item or decoded.startswith(f"{item}/") for item in relevant_paths):
            continue
        digest.update(raw_path)
        try:
            path = Path(decoded)
            components = [path, *path.parents]
            if any(component.is_symlink() for component in components) or not stat.S_ISREG(
                path.lstat().st_mode
            ):
                raise SourceFingerprintError(
                    f"Relevant untracked path must be regular with no symlink ancestor: {decoded}"
                )
            digest.update(path.read_bytes())
        except OSError as error:
            raise SourceFingerprintError(
                f"Relevant untracked path could not be fingerprinted: {decoded}"
            ) from error
    return digest.hexdigest()


def current_source_fingerprint() -> str:
    """Compute current identity, with a deterministic hook available only inside pytest."""
    test_value = os.environ.get("IMMICH_BENCHMARK_TEST_FINGERPRINT")
    fingerprint = (
        test_value
        if test_value is not None and os.environ.get("PYTEST_CURRENT_TEST")
        else source_fingerprint()
    )
    return _require_exact_source_fingerprint(fingerprint)


def _owned_batch(parent: Path, batch: Path) -> bool:
    return (
        batch.parent == parent
        and re.fullmatch(r"batch\.[A-Za-z0-9]{8}", batch.name) is not None
        and batch.is_dir()
        and not batch.is_symlink()
    )


def _read_batch_manifest(batch: Path) -> dict[str, Any] | None:
    manifest_path = batch / "batch-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _manifest_expected_names(manifest: dict[str, Any]) -> set[str] | None:
    expected = manifest.get("expected")
    if (
        not isinstance(expected, list)
        or not expected
        or not all(isinstance(name, str) for name in expected)
    ):
        return None
    names = set(expected)
    return names if len(names) == len(expected) else None


def _load_benchmark_envelopes(
    batch: Path, expected: set[str], *, exact: bool, committed: bool = True
) -> list[dict[str, Any]] | None:
    allowed_entries = expected | ({"batch-manifest.json"} if committed else set())
    if exact and {path.name for path in batch.iterdir()} != allowed_entries:
        return None
    files = {
        path.name: path
        for path in batch.glob("benchmark-*.json")
        if path.is_file() and not path.is_symlink()
    }
    if (set(files) != expected) if exact else (not expected.issubset(files)):
        return None
    try:
        payloads = [json.loads(files[name].read_text()) for name in expected]
    except (OSError, json.JSONDecodeError):
        return None
    return payloads if all(isinstance(payload, dict) for payload in payloads) else None


def _envelope_has_identity(payload: dict[str, Any], revision: object, fingerprint: object) -> bool:
    return bool(
        isinstance(payload, dict)
        and isinstance(payload.get("benchmarks"), list)
        and payload["benchmarks"]
        and isinstance(payload.get("results"), list)
        and payload["results"]
        and payload.get("git_revision") == revision
        and payload.get("source_fingerprint") == fingerprint
    )


def _validated_batch_manifest(batch: Path, *, exact: bool) -> dict[str, Any] | None:
    """Return a manifest only when its private batch still contains every declared envelope."""
    manifest = _read_batch_manifest(batch) if batch.is_dir() and not batch.is_symlink() else None
    expected = _manifest_expected_names(manifest) if manifest is not None else None
    payloads = (
        _load_benchmark_envelopes(batch, expected, exact=exact) if expected is not None else None
    )
    if manifest is None or payloads is None:
        return None
    revision = manifest.get("git_revision")
    fingerprint = manifest.get("source_fingerprint")
    return (
        manifest
        if all(_envelope_has_identity(payload, revision, fingerprint) for payload in payloads)
        else None
    )


def write_benchmark_batch_manifest(
    batch: Path,
    expected: Sequence[str],
    *,
    revision: str,
    fingerprint: str,
    started_ns: int,
) -> None:
    """Validate one complete private batch and create its commit manifest exclusively."""
    _require_exact_source_fingerprint(fingerprint)
    if not batch.is_dir() or batch.is_symlink():
        raise ValueError("benchmark batch must be a private regular directory")
    expected_names = set(expected)
    payloads = _load_benchmark_envelopes(batch, expected_names, exact=True, committed=False)
    if payloads is None:
        raise ValueError("incomplete benchmark batch")
    if not all(_envelope_has_identity(payload, revision, fingerprint) for payload in payloads):
        raise ValueError("incomplete or stale benchmark batch")
    try:
        for payload in payloads:
            _validate_submission_payload(payload, head_revision=revision)
    except ValueError as error:
        raise ValueError("invalid benchmark measurements") from error
    manifest = {
        "expected": sorted(expected_names),
        "git_revision": revision,
        "source_fingerprint": fingerprint,
        "started_ns": started_ns,
    }
    with (batch / "batch-manifest.json").open("x") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")


def publish_benchmark_batch(parent: Path, batch: Path, *, started_ns: int) -> None:
    """Atomically point at the newest-started valid batch without following unsafe paths."""
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("benchmark output root must be a regular directory")
    if not _owned_batch(parent, batch) or not _is_valid_owned_batch(batch):
        raise ValueError("benchmark publication requires a valid owned batch")
    current = parent / "current-batch"
    previous_started = -1
    if current.is_file() and not current.is_symlink():
        try:
            previous_batch = Path(current.read_text())
            previous_manifest = (
                _validated_batch_manifest(previous_batch, exact=True)
                if _owned_batch(parent, previous_batch) and _is_valid_owned_batch(previous_batch)
                else None
            )
            if previous_manifest is not None:
                previous_started = int(previous_manifest.get("started_ns", -1))
        except (OSError, TypeError, ValueError):
            previous_started = -1
    descriptor, temporary_name = tempfile.mkstemp(prefix=".current-batch.", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(batch))
        if started_ns >= previous_started:
            os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)


def _submission_batch(parent: Path) -> Path:
    parent = require_benchmark_temporary_root(parent, create=False)
    current = parent / "current-batch"
    if not current.is_file() or current.is_symlink():
        raise ValueError("no current complete benchmark batch")
    batch = Path(current.read_text())
    if not _owned_batch(parent, batch):
        raise ValueError("unsafe benchmark batch pointer")
    return batch


def _summary_runs(summary: object) -> tuple[dict[str, Any], ...]:
    warmup = summary.get("warmup") if isinstance(summary, dict) else None
    repetitions = summary.get("repetitions") if isinstance(summary, dict) else None
    if not isinstance(warmup, dict) or not isinstance(repetitions, list) or len(repetitions) < 3:
        raise ValueError("each result needs one warmup and at least three repetitions")
    if not all(isinstance(run, dict) for run in repetitions):
        raise ValueError("benchmark repetitions must be objects")
    return (warmup, *repetitions)


def _validate_submission_payload(payload: dict[str, Any], *, head_revision: str) -> None:
    summaries = payload.get("results")
    if not isinstance(summaries, list) or not summaries:
        raise ValueError("benchmark envelope must contain results")
    for summary in summaries:
        for run in _summary_runs(summary):
            if any(not run.get(key) for key in REQUIRED_REPRODUCTION_KEYS):
                raise ValueError("benchmark measurement lacks reproduction fields")
            if run["git_revision"] != head_revision:
                raise ValueError("benchmark measurements do not share current HEAD")


def _is_valid_owned_batch(batch: Path) -> bool:
    manifest = _validated_batch_manifest(batch, exact=True)
    expected = _manifest_expected_names(manifest) if manifest is not None else None
    payloads = (
        _load_benchmark_envelopes(batch, expected, exact=True) if expected is not None else None
    )
    revision = manifest.get("git_revision") if manifest is not None else None
    if payloads is None or not isinstance(revision, str):
        return False
    try:
        for payload in payloads:
            _validate_submission_payload(payload, head_revision=revision)
    except ValueError:
        return False
    return True


def validate_benchmark_submission_batch(
    parent: Path,
    *,
    revision: str,
    fingerprint: str,
    head_revision: str,
) -> Path:
    """Validate the pointer, manifest, and every envelope before any external dispatch."""
    batch = _submission_batch(parent)
    manifest = _validated_batch_manifest(batch, exact=True)
    expected_names = sorted(_manifest_expected_names(manifest) or set()) if manifest else []
    allowed_suites = (
        ["benchmark-assembly.json", "benchmark-titles.json"],
        ["benchmark-assembly.json", "benchmark-pipeline.json", "benchmark-titles.json"],
    )
    valid_manifest = (
        manifest is not None
        and expected_names in allowed_suites
        and manifest.get("expected") == expected_names
        and manifest.get("git_revision") == revision
        and manifest.get("source_fingerprint") == fingerprint
    )
    if not valid_manifest:
        raise ValueError("invalid current benchmark batch manifest")
    payloads = _load_benchmark_envelopes(batch, set(expected_names), exact=True)
    if payloads is None:
        raise ValueError("invalid enriched benchmark envelope")
    for payload in payloads:
        _validate_submission_payload(payload, head_revision=head_revision)
    return batch


def merge_benchmark_batch(batch: Path) -> dict[str, Any]:
    """Merge the exact manifest-owned suite envelopes into one workflow-dispatch payload."""
    manifest = _validated_batch_manifest(batch, exact=True)
    expected = _manifest_expected_names(manifest) if manifest is not None else None
    payloads = (
        _load_benchmark_envelopes(batch, expected, exact=True) if expected is not None else None
    )
    if manifest is None or payloads is None:
        raise ValueError("cannot merge an invalid benchmark batch")
    return {
        "benchmarks": [metric for payload in payloads for metric in payload["benchmarks"]],
        "results": [summary for payload in payloads for summary in payload["results"]],
        "git_revision": manifest["git_revision"],
        "source_fingerprint": manifest["source_fingerprint"],
    }
