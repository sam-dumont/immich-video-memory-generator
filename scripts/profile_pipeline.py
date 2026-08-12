#!/usr/bin/env python3
"""Profile isolated, reproducible local video-assembly scenarios."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import os
import platform
import pstats
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, TypedDict, cast
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "controlled-cold": {"duration": 3, "clip_count": 2},
    "controlled-warm": {"duration": 3, "clip_count": 2},
    "controlled-tiny": {"duration": 1, "clip_count": 2},
}


class AssemblyConfigMetadata(TypedDict):
    codec: str
    crf: int
    transition: str
    transition_duration_seconds: float


CONFIG: dict[str, str | int] = {"frame_rate": 30, "resolution": "1280x720"}
ASSEMBLY_CONFIG: AssemblyConfigMetadata = {
    "codec": "h264",
    "crf": 28,
    "transition": "crossfade",
    "transition_duration_seconds": 0.3,
}
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
IDENTITY_FIELDS = (
    "scenario",
    "clip_count",
    "resolution",
    "input_duration_seconds",
    "codec",
    "frame_rate",
    "cache_mode",
    "python_version",
    "platform",
    "cpu",
)


def count_comparable_baselines(data: object, current_projection: list[dict[str, object]]) -> int:
    """Count cached suites containing every current benchmark name and identity."""
    if not isinstance(data, dict):
        return 0
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return 0
    histories = entries.get("Benchmark")
    if not isinstance(histories, list):
        return 0
    expected = {(entry.get("name"), entry.get("extra")) for entry in current_projection}
    if not expected or any(
        not isinstance(name, str) or not isinstance(extra, str) for name, extra in expected
    ):
        return 0
    comparable = 0
    for suite in histories:
        if not isinstance(suite, dict):
            continue
        benches = suite.get("benches")
        if not isinstance(benches, list):
            continue
        observed = {
            (bench.get("name"), bench.get("extra")) for bench in benches if isinstance(bench, dict)
        }
        if expected <= observed:
            comparable += 1
    return comparable


def _nonempty_string(result: dict[str, object], field: str) -> str:
    value = result.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _finite_number(result: dict[str, object], field: str, *, positive: bool = False) -> float:
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    numeric = float(value)
    if positive and numeric <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return numeric


def _identity_extra(result: dict[str, object]) -> str:
    identity: dict[str, object] = {}
    for field in IDENTITY_FIELDS:
        if field in {"clip_count"}:
            value = result.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
            identity[field] = value
        elif field in {"input_duration_seconds", "frame_rate"}:
            identity[field] = _finite_number(result, field, positive=True)
        else:
            identity[field] = _nonempty_string(result, field)
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def benchmark_comparison_projection(submitted: object) -> list[dict[str, object]]:
    """Validate full assembly metadata and return benchmark-action comparison entries."""
    if not isinstance(submitted, dict):
        raise ValueError("full benchmark results are required")
    results = submitted.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("no full benchmark results")
    projection: list[dict[str, object]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("benchmark result must be an object")
        missing = REQUIRED_REPRODUCTION_KEYS - result.keys()
        if missing:
            raise ValueError(f"{result.get('scenario', 'unknown')}: missing {sorted(missing)}")
        raw_repetitions = result["raw_repetition_seconds"]
        if not isinstance(raw_repetitions, list) or len(raw_repetitions) != 3:
            raise ValueError("raw_repetition_seconds must be a list of exactly three values")
        for value in raw_repetitions:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                raise ValueError("raw_repetition_seconds must contain finite numeric values")
        _finite_number(result, "warmup_wall_seconds", positive=True)
        median = _finite_number(result, "median_wall_seconds", positive=True)
        scenario = _nonempty_string(result, "scenario")
        extra = _identity_extra(result)
        identity_digest = hashlib.sha256(extra.encode()).hexdigest()[:12]
        projection.append(
            {
                "name": f"{scenario} [{identity_digest}]",
                "unit": "seconds",
                "value": median,
                "extra": extra,
            }
        )
    return projection


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.repetitions < 3:
        parser.error("--repetitions must be at least three")
    return arguments


def _isolated_output_dir(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    test_root = os.environ.get("IMMICH_MEMORIES_PROFILE_TEST_ROOT")
    if test_root:
        root = Path(test_root).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"--output-dir must be inside test root {root}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _tool_identity(tool: str) -> str:
    try:
        return subprocess.run(
            [tool, "-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def _command_output(command: list[str]) -> str | None:
    try:
        return (
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_fingerprint() -> str:
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


def _environment() -> dict[str, str]:
    return {
        "cpu": _cpu_fingerprint(),
        "ffmpeg": _tool_identity("ffmpeg"),
        "ffprobe": _tool_identity("ffprobe"),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def _make_clips(directory: Path, *, duration: int, clip_count: int) -> list[Path]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for controlled profiling")
    directory.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for index in range(clip_count):
        path = directory / f"input-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={CONFIG['resolution']}:rate={CONFIG['frame_rate']}:duration={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={220 + index * 110}:duration={duration}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        clips.append(path)
    return clips


def _make_assembler() -> Any:
    from immich_memories.processing.assembly_config import (
        AssemblySettings,
        TransitionType,
        standalone_assembly_encoding_plan,
    )
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(
        AssemblySettings(
            encoding_plan=standalone_assembly_encoding_plan(ASSEMBLY_CONFIG["crf"]),
            transition=TransitionType.CROSSFADE,
            transition_duration=ASSEMBLY_CONFIG["transition_duration_seconds"],
            normalize_clip_audio=False,
            auto_resolution=False,
            target_resolution=(1280, 720),
        )
    )


def _assemble(
    clips: list[Path], output: Path, *, duration: int, assembler: Any | None = None
) -> Path:
    from immich_memories.processing.assembly_config import AssemblyClip

    assembler = assembler or _make_assembler()
    return assembler.assemble(
        [
            AssemblyClip(path=clip, duration=float(duration), asset_id=f"profile-{index}")
            for index, clip in enumerate(clips)
        ],
        output,
    )


def _analyze(video_path: Path, *, temp_dir: Path) -> None:
    """Run hermetic visual and audio analysis without LLM or network clients."""
    from immich_memories.analysis.scenes import SceneDetector
    from immich_memories.analysis.segment_generation import detect_audio_boundaries
    from immich_memories.config_models import AnalysisConfig

    config = AnalysisConfig()
    SceneDetector(analysis_config=config).detect(video_path, extract_keyframes=False)
    temp_dir.mkdir(parents=True, exist_ok=True)
    named_temporary_file = partial(tempfile.NamedTemporaryFile, dir=temp_dir)
    with patch(
        "immich_memories.analysis.silence_detection.tempfile.NamedTemporaryFile",
        named_temporary_file,
    ):
        detect_audio_boundaries(
            video_path,
            silence_threshold_db=config.silence_threshold_db,
            min_silence_duration=config.min_silence_duration,
        )


def _analysis_config() -> dict[str, object]:
    from immich_memories.config_models import AnalysisConfig

    config = AnalysisConfig()
    return {
        "audio_boundaries": True,
        "adaptive_scene_detector": True,
        "extract_keyframes": False,
        "llm_clients_constructed": False,
        "scene_detector": "SceneDetector",
        "silence_threshold_db": config.silence_threshold_db,
        "min_silence_duration_seconds": config.min_silence_duration,
        "min_scene_duration_seconds": config.min_scene_duration,
        "scene_threshold": config.scene_threshold,
    }


def _write_table(profile_path: Path, output_path: Path, sort: str) -> None:
    with output_path.open("w") as stream:
        pstats.Stats(str(profile_path), stream=stream).strip_dirs().sort_stats(sort).print_stats(50)


def _subprocess_wait_estimate_seconds(stats: pstats.Stats) -> float:
    """Estimate non-overlapping waits from subprocess.run and direct Popen.wait calls."""
    estimate = 0.0
    raw_stats = cast(
        dict[tuple[str, int, str], tuple[object, object, object, float, dict]], vars(stats)["stats"]
    )
    for function, values in raw_stats.items():
        if Path(function[0]).name != "subprocess.py":
            continue
        if function[2] == "run":
            estimate += values[3]
        elif function[2] == "wait":
            callers = values[4]
            estimate += sum(
                cast(tuple[object, object, object, float], caller_values)[3]
                for caller, caller_values in callers.items()
                if Path(caller[0]).name != "subprocess.py"
            )
    return estimate


def _profile_stage(
    *,
    scenario: str,
    index: int,
    stage: str,
    output_dir: Path,
    operation: Callable[[], None],
) -> tuple[float, float]:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.runcall(operation)
    elapsed = time.perf_counter() - started
    profile_path = output_dir / f"{scenario}-{index}-{stage}.prof"
    profiler.dump_stats(profile_path)
    _write_table(
        profile_path, output_dir / f"{scenario}-{index}-{stage}-cumulative.txt", "cumulative"
    )
    _write_table(
        profile_path,
        output_dir / f"{scenario}-{index}-{stage}-self.txt",
        "tottime",
    )
    return elapsed, _subprocess_wait_estimate_seconds(pstats.Stats(str(profile_path)))


def _run_warmup(
    *, scenario: str, output_dir: Path, clips: list[Path], duration: int, assembler: Any | None
) -> dict[str, float]:
    work_dir = output_dir / "work" / f"{scenario}-warmup"
    work_dir.mkdir(parents=True, exist_ok=True)
    assembled_path = work_dir / "assembled.mp4"
    started = time.perf_counter()
    _assemble(clips, assembled_path, duration=duration, assembler=assembler)
    assembly_seconds = time.perf_counter() - started
    started = time.perf_counter()
    _analyze(assembled_path, temp_dir=work_dir / "analysis-tmp")
    return {"assembly": assembly_seconds, "analysis": time.perf_counter() - started}


def main() -> int:
    arguments = _parse_arguments()
    try:
        output_dir = _isolated_output_dir(arguments.output_dir)
        details = SCENARIOS[arguments.scenario]
        warm_assembler = None
        warm_clips = None
        if arguments.scenario == "controlled-warm":
            warm_clips = _make_clips(output_dir / "work" / "warm-inputs", **details)
            warm_assembler = _make_assembler()
        warmup_clips = warm_clips or _make_clips(
            output_dir / "work" / f"{arguments.scenario}-warmup-inputs", **details
        )
        warmup_timings = _run_warmup(
            scenario=arguments.scenario,
            output_dir=output_dir,
            clips=warmup_clips,
            duration=details["duration"],
            assembler=warm_assembler,
        )
        stage_timings: dict[str, list[tuple[float, float]]] = {"assembly": [], "analysis": []}
        for index in range(1, arguments.repetitions + 1):
            work_dir = output_dir / "work" / f"{arguments.scenario}-{index}"
            work_dir.mkdir(parents=True, exist_ok=True)
            clips = warm_clips or _make_clips(work_dir / "inputs", **details)
            assembled_path = work_dir / "assembled.mp4"

            def assemble_stage(
                clips: list[Path] = clips,
                assembled_path: Path = assembled_path,
            ) -> None:
                _assemble(
                    clips,
                    assembled_path,
                    duration=details["duration"],
                    assembler=warm_assembler,
                )

            stage_timings["assembly"].append(
                _profile_stage(
                    scenario=arguments.scenario,
                    index=index,
                    stage="assembly",
                    output_dir=output_dir,
                    operation=assemble_stage,
                )
            )

            def analysis_stage(
                assembled_path: Path = assembled_path,
                work_dir: Path = work_dir,
            ) -> None:
                _analyze(assembled_path, temp_dir=work_dir / "analysis-tmp")

            stage_timings["analysis"].append(
                _profile_stage(
                    scenario=arguments.scenario,
                    index=index,
                    stage="analysis",
                    output_dir=output_dir,
                    operation=analysis_stage,
                )
            )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"profile failed: {error}", file=sys.stderr)
        return 2

    metadata = {
        "command": [sys.executable, *sys.argv],
        "config": {
            "cache_mode": "warm" if arguments.scenario == "controlled-warm" else "cold",
            "clip_count": details["clip_count"],
            "duration_seconds": details["duration"],
            "assembly": ASSEMBLY_CONFIG,
            "analysis": _analysis_config(),
            **CONFIG,
        },
        "cprofile_note": (
            "cProfile includes Python call and wait time; it cannot inspect child-process internals."
        ),
        "environment": _environment(),
        "git_revision": _git_revision(),
        "scenario": arguments.scenario,
        "stage_wall_seconds": {
            stage: [wall for wall, _ in timings] for stage, timings in stage_timings.items()
        },
        "warmup_stage_wall_seconds": warmup_timings,
        "subprocess_wait_estimate_seconds": {
            stage: [wait for _, wait in timings] for stage, timings in stage_timings.items()
        },
        "subprocess_wait_note": (
            "estimated from cProfile's subprocess.run and Popen.wait cumulative timings; "
            "it includes wait time but cannot inspect child-process internals."
        ),
    }
    (output_dir / f"{arguments.scenario}-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
