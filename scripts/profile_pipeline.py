#!/usr/bin/env python3
"""Profile isolated, reproducible pipeline slices with only the standard library.

The controlled scenarios create synthetic 720p input under ``--output-dir`` and never consult an
Immich server, a user cache, or a project output directory.  They are deliberately a profiler,
not a benchmark result publisher: only profile artifacts and a local JSON summary are written.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import platform
import pstats
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLLED_CONFIG = {
    "audio": "silent stereo AAC",
    "codec": "h264",
    "duration_seconds": 1.0,
    "frame_rate": 24.0,
    "input_count": 2,
    "resolution": "1280x720",
    "transition": "crossfade 0.1s",
    "video_crf": 28,
}
TINY_CONFIG = {"height": 72, "width": 128}


def _git_revision() -> str:
    """Return the current repository revision without a third-party dependency."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _git_describe() -> str:
    """Capture revision plus dirty state so an unstaged profile is not mistaken for clean HEAD."""
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _temporary_root(path: Path) -> Path:
    """Validate a caller-selected output path before creating anything."""
    if path.is_symlink():
        raise ValueError("profile output root must not be a symlink")
    output = path.resolve()
    temporary_roots = {Path(tempfile.gettempdir()).resolve()}
    temporary_roots.update(
        candidate.resolve()
        for candidate in (Path("/tmp"), Path("/var/tmp"))  # noqa: S108 -- allowed roots only
        if candidate.is_dir()
    )
    if not any(output.is_relative_to(root) for root in temporary_roots):
        choices = ", ".join(str(root) for root in sorted(temporary_roots))
        raise ValueError(f"profile output must stay below a temporary root: {choices}")

    test_root_value = os.environ.get("IMMICH_PROFILE_TEST_ROOT")
    if test_root_value:
        test_root = Path(test_root_value).resolve()
        if not output.is_relative_to(test_root):
            raise ValueError(f"profile output must stay below the test root: {test_root}")

    output.mkdir(parents=True, exist_ok=True)
    return output


def _run(command: list[str]) -> None:
    """Run a controlled local media command without inheriting a project output path."""
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def _prepare_controlled_inputs(root: Path) -> tuple[Path, Path]:
    """Create two deterministic, local-only 720p clips outside profile timing."""
    inputs = root / "controlled-inputs"
    inputs.mkdir(exist_ok=True)
    clips = (inputs / "one-with-audio.mp4", inputs / "two-with-audio.mp4")
    colors = ("0x224466", "0x664422")
    for clip, color in zip(clips, colors, strict=True):
        # A new private stage is used for every generation, so regeneration is intentional: a
        # corrupt prior fixture can never become profile input.
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=1280x720:r=24:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            ]
        )
        _validate_controlled_clip(clip)
    return clips


def _validate_controlled_clip(clip: Path) -> None:
    """Fail closed unless generated fixtures are the declared h264/720p/24fps/AAC inputs."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(clip)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    data = json.loads(probe.stdout)
    streams = data.get("streams", [])
    video: dict[str, Any] = next(
        (item for item in streams if item.get("codec_type") == "video"), {}
    )
    audio: dict[str, Any] = next(
        (item for item in streams if item.get("codec_type") == "audio"), {}
    )
    duration = float(data.get("format", {}).get("duration", 0))
    valid = (
        video.get("codec_name") == "h264"
        and (video.get("width"), video.get("height")) == (1280, 720)
        and video.get("r_frame_rate") == "24/1"
        and audio.get("codec_name") == "aac"
        and 0.9 <= duration <= 1.1
    )
    if not valid:
        raise ValueError(f"controlled fixture failed ffprobe validation: {clip}")


def _input_fingerprint(clips: tuple[Path, Path]) -> str:
    """Bind controlled metadata to the exact deterministic source bytes used in this generation."""
    digest = hashlib.sha256()
    for clip in clips:
        digest.update(clip.name.encode())
        digest.update(clip.read_bytes())
    return digest.hexdigest()


def _tiny_operation() -> int:
    """Run a deterministic pure-Python operation for smoke-testing the profile machinery."""
    return sum((row * column) % 17 for row in range(72) for column in range(128))


def _controlled_operation(
    clips: tuple[Path, Path], output_path: Path, *, probe_cache: Any | None = None
) -> None:
    """Analyse input identity then assemble synthetic 720p media through the production assembler."""
    from immich_memories.processing.assembly_config import (
        AssemblyClip,
        AssemblySettings,
        TransitionType,
    )
    from immich_memories.processing.clip_probing import get_video_info
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.video_assembler import VideoAssembler

    active_probe_cache = probe_cache or ProbeCache()
    for clip in clips:
        get_video_info(clip, probe_cache=active_probe_cache)
    assembler = VideoAssembler(
        AssemblySettings(
            transition=TransitionType.CROSSFADE,
            transition_duration=0.1,
            output_crf=28,
            preserve_hdr=False,
            normalize_clip_audio=False,
            auto_resolution=False,
            target_resolution=(1280, 720),
        ),
        probe_cache=active_probe_cache,
    )
    assembler.assemble(
        [AssemblyClip(path=clip, duration=1.0, asset_id=clip.stem) for clip in clips], output_path
    )


def _write_profile_reports(profile: cProfile.Profile, raw_path: Path) -> None:
    """Persist the raw data plus independent top-50 cumulative and self-time reports."""
    profile.dump_stats(str(raw_path))
    reports = (("cumulative", pstats.SortKey.CUMULATIVE), ("self-time", pstats.SortKey.TIME))
    for suffix, sort_key in reports:
        report_path = raw_path.with_name(f"{raw_path.stem}-{suffix}.txt")
        with report_path.open("w") as stream, redirect_stdout(stream):
            pstats.Stats(str(raw_path), stream=stream).strip_dirs().sort_stats(
                sort_key
            ).print_stats(50)


def _profile_once(
    operation: Callable[[], Any],
    *,
    root: Path,
    scenario: str,
    label: str,
) -> float:
    """Profile one warm-up or measured invocation and return its elapsed wall time."""
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    try:
        operation()
    finally:
        profiler.disable()
    elapsed = time.perf_counter() - started
    _write_profile_reports(profiler, root / f"{scenario}-{label}.prof")
    if os.environ.get("IMMICH_PROFILE_FAIL_AFTER") == label:
        raise RuntimeError(f"injected profile failure after {label}")
    return elapsed


def _metadata(*, command: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Capture every detail needed to reproduce a local profile run."""
    return {
        "command": command,
        "config": config,
        "git_revision": _git_revision(),
        "git_describe": _git_describe(),
        "environment": {
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
    }


def _is_owned_generation_artifact(*, scenario: str, name: str) -> bool:
    """Recognize only generation files emitted by this profiler."""
    suffix = r"(?:metadata\.json|summary\.json|(?:warmup|rep-[1-9][0-9]*)(?:\.prof|-cumulative\.txt|-self-time\.txt))"
    return re.fullmatch(rf"{re.escape(scenario)}-[a-z0-9_]{{8}}-{suffix}", name) is not None


def _remove_unreferenced_artifacts(
    *, root: Path, scenario: str, current_artifacts: set[str]
) -> None:
    """Best-effort cleanup after the stable manifest commits a complete generation."""
    for candidate in root.iterdir():
        if candidate.name in current_artifacts or not _is_owned_generation_artifact(
            scenario=scenario, name=candidate.name
        ):
            continue
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        try:
            candidate.unlink()
        except OSError:
            pass


def _run_scenario(args: argparse.Namespace, root: Path) -> None:
    """Run one cold priming pass and at least three measured profile repetitions."""
    test_generation = os.environ.get("IMMICH_PROFILE_TEST_GENERATION")
    if test_generation and os.environ.get("IMMICH_PROFILE_TEST_ROOT"):
        if re.fullmatch(r"[a-z0-9_]{8}", test_generation) is None:
            raise ValueError("test generation must be eight lowercase name characters")
        stage = root / f".{args.scenario}-{test_generation}"
        stage.mkdir(mode=0o700)
    else:
        stage = Path(tempfile.mkdtemp(prefix=f".{args.scenario}-", dir=root))
    try:
        _run_staged_scenario(args, root=root, stage=stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _run_staged_scenario(args: argparse.Namespace, *, root: Path, stage: Path) -> None:
    """Produce a complete private generation before atomically publishing each final artifact."""
    config: dict[str, Any] = TINY_CONFIG if args.scenario == "tiny" else CONTROLLED_CONFIG.copy()
    if args.scenario == "tiny":
        operation: Callable[[], Any] = _tiny_operation
    else:
        clips = _prepare_controlled_inputs(stage)
        config["input_fingerprint"] = _input_fingerprint(clips)
        output_directory = stage / f"{args.scenario}-outputs"
        output_directory.mkdir(exist_ok=True)
        warm_cache = None
        if args.scenario == "controlled-warm":
            from immich_memories.processing.probe_cache import ProbeCache

            warm_cache = ProbeCache()
            config["cache_mode"] = "one caller-owned ProbeCache reused across repetitions"
        else:
            config["cache_mode"] = "fresh caller-owned ProbeCache per repetition"

        def operation() -> None:
            output_path = output_directory / f"{time.time_ns()}.mp4"
            _controlled_operation(clips, output_path, probe_cache=warm_cache)

    warmup = [_profile_once(operation, root=stage, scenario=args.scenario, label="warmup")]
    repetitions = [
        _profile_once(operation, root=stage, scenario=args.scenario, label=f"rep-{index}")
        for index in range(1, args.repetitions + 1)
    ]
    metadata_path = stage / f"{args.scenario}-metadata.json"
    summary_path = stage / f"{args.scenario}-summary.json"
    metadata_path.write_text(
        json.dumps(_metadata(command=sys.argv, config=config), indent=2) + "\n"
    )
    summary_path.write_text(
        json.dumps(
            {
                "scenario": args.scenario,
                "warmup": warmup,
                "repetitions": repetitions,
                "median_wall_seconds": statistics.median(repetitions),
            },
            indent=2,
        )
        + "\n"
    )
    artifacts = sorted(
        path
        for path in stage.glob(f"{args.scenario}-*")
        if path.is_file() and not path.is_symlink()
    )
    expected_count = 2 + (args.repetitions + 1) * 3
    if len(artifacts) != expected_count or any(path.stat().st_size <= 0 for path in artifacts):
        raise ValueError("profile generation did not produce a complete nonempty artifact set")
    generation = stage.name.removeprefix(f".{args.scenario}-")
    published = {
        path: f"{args.scenario}-{generation}-{path.name.removeprefix(f'{args.scenario}-')}"
        for path in artifacts
    }
    manifest = {
        "artifacts": sorted(published.values()),
        "generation": generation,
        "scenario": args.scenario,
        "source": _metadata(command=sys.argv, config=config),
    }
    manifest_path = stage / f"{args.scenario}-{generation}-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    previous_manifest = root / f"{args.scenario}-manifest.json"
    # Each artifact has a generation-unique target.  Publishing the stable manifest last is the
    # sole commit point: a mid-publish failure leaves every byte referenced by the old manifest.
    for artifact, destination_name in published.items():
        os.replace(artifact, root / destination_name)
    if os.environ.get("IMMICH_PROFILE_FAIL_DURING_PUBLISH"):
        raise RuntimeError("injected profile failure during publication")
    descriptor, manifest_commit_name = tempfile.mkstemp(
        prefix=f".{args.scenario}-manifest.", suffix=".tmp", dir=root
    )
    manifest_commit = Path(manifest_commit_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(manifest_path.read_text())
        os.replace(manifest_commit, previous_manifest)
    finally:
        manifest_commit.unlink(missing_ok=True)
    _remove_unreferenced_artifacts(
        root=root,
        scenario=args.scenario,
        current_artifacts=set(manifest["artifacts"]),
    )
    print(f"{args.scenario}: median {statistics.median(repetitions):.3f}s")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("tiny", "controlled-cold", "controlled-warm"),
        required=True,
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.repetitions < 3:
        _parser().error("--repetitions must be at least 3")
    try:
        root = _temporary_root(args.output_dir)
        _run_scenario(args, root)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"profile failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
