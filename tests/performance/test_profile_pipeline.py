"""Contracts for isolated, reproducible pipeline profiles."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

PROFILE_SCRIPT = Path(__file__).parents[2] / "scripts" / "profile_pipeline.py"


def _profile_artifact(root: Path, scenario: str, suffix: str) -> Path:
    """Resolve a committed generation artifact through its stable manifest pointer."""
    manifest = json.loads((root / f"{scenario}-manifest.json").read_text())
    return root / next(name for name in manifest["artifacts"] if name.endswith(suffix))


def _run_profile_script(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the profile CLI as a user would, without importing its implementation."""
    command = [sys.executable, str(PROFILE_SCRIPT), *args]
    return subprocess.run(
        command,
        cwd=PROFILE_SCRIPT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_profile_cli_help_needs_no_output_directory() -> None:
    """Removing argparse help must break discoverability without creating profile output."""
    result = _run_profile_script("--help")

    assert result.returncode == 0
    assert "--output-dir" in result.stdout


def test_profile_cli_requires_an_explicit_output_directory() -> None:
    """Dropping the required output switch must not fall back to a user-owned directory."""
    result = _run_profile_script("--scenario", "tiny")

    assert result.returncode != 0
    assert "--output-dir" in result.stderr


def test_profile_cli_test_root_hook_refuses_an_outside_directory(tmp_path: Path) -> None:
    """Removing the test-root check would let the test harness profile outside its sandbox."""
    allowed_root = tmp_path / "allowed"
    environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(allowed_root)}

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(tmp_path / "outside"),
        env=environment,
    )

    assert result.returncode != 0
    assert "test root" in result.stderr
    assert not (tmp_path / "outside").exists()


def test_profile_cli_tiny_scenario_keeps_state_and_artifacts_in_the_given_root(
    tmp_path: Path,
) -> None:
    """A profiler that writes a cache, database, or output elsewhere breaks isolation."""
    output_root = tmp_path / "profiles"
    user_state = tmp_path / "user-state"
    environment = {
        **os.environ,
        "HOME": str(user_state / "home"),
        "XDG_CACHE_HOME": str(user_state / "cache"),
        "XDG_CONFIG_HOME": str(user_state / "config"),
        "XDG_DATA_HOME": str(user_state / "data"),
        "IMMICH_PROFILE_TEST_ROOT": str(tmp_path),
    }

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--repetitions",
        "3",
        "--output-dir",
        str(output_root),
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    metadata = json.loads(_profile_artifact(output_root, "tiny", "metadata.json").read_text())
    assert metadata["command"][-2:] == ["--output-dir", str(output_root)]
    assert metadata["config"] == {"height": 72, "width": 128}
    assert metadata["git_revision"]
    assert metadata["git_describe"]
    assert metadata["environment"]["python_version"]
    summary = json.loads(_profile_artifact(output_root, "tiny", "summary.json").read_text())
    assert len(summary["warmup"]) == 1
    assert len(summary["repetitions"]) == 3
    for label in ("warmup", "rep-1", "rep-2", "rep-3"):
        assert _profile_artifact(output_root, "tiny", f"{label}.prof").is_file()
        assert (
            "function calls"
            in _profile_artifact(output_root, "tiny", f"{label}-cumulative.txt").read_text()
        )
        assert (
            "function calls"
            in _profile_artifact(output_root, "tiny", f"{label}-self-time.txt").read_text()
        )
    assert not user_state.exists()


def test_profile_cli_controlled_metadata_records_reproductive_media_configuration(
    tmp_path: Path,
) -> None:
    """A profile missing input/encoding/cache semantics cannot be independently reproduced."""
    output_root = tmp_path / "profiles"
    environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)}

    result = _run_profile_script(
        "--scenario",
        "controlled-warm",
        "--repetitions",
        "3",
        "--output-dir",
        str(output_root),
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(
        _profile_artifact(output_root, "controlled-warm", "metadata.json").read_text()
    )["config"]
    input_fingerprint = config.pop("input_fingerprint")
    assert len(input_fingerprint) == 64
    assert config == {
        "audio": "silent stereo AAC",
        "cache_mode": "one caller-owned ProbeCache reused across repetitions",
        "codec": "h264",
        "duration_seconds": 1.0,
        "frame_rate": 24.0,
        "input_count": 2,
        "resolution": "1280x720",
        "transition": "crossfade 0.1s",
        "video_crf": 28,
    }


def test_profile_cli_uses_the_statistical_median_for_four_measurements(tmp_path: Path) -> None:
    """Selecting the upper middle value instead of a median would skew even-sized runs."""
    output_root = tmp_path / "profiles"
    environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)}

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--repetitions",
        "4",
        "--output-dir",
        str(output_root),
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(_profile_artifact(output_root, "tiny", "summary.json").read_text())
    assert summary["median_wall_seconds"] == statistics.median(summary["repetitions"])


def test_profile_failure_keeps_the_previous_complete_generation(tmp_path: Path) -> None:
    """A failed rerun must not publish partial raw profiles over the prior manifest generation."""
    output_root = tmp_path / "profiles"
    base_environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)}
    first = _run_profile_script(
        "--scenario", "tiny", "--output-dir", str(output_root), env=base_environment
    )
    assert first.returncode == 0, first.stderr
    previous_manifest = (output_root / "tiny-manifest.json").read_text()
    failed = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={**base_environment, "IMMICH_PROFILE_FAIL_AFTER": "rep-1"},
    )

    assert failed.returncode != 0
    assert (output_root / "tiny-manifest.json").read_text() == previous_manifest
    manifest = json.loads(previous_manifest)
    assert all((output_root / name).is_file() for name in manifest["artifacts"])


def test_profile_publish_failure_keeps_the_previous_manifest_bytes(tmp_path: Path) -> None:
    """A failure after new artifacts move but before commit cannot corrupt the prior generation."""
    output_root = tmp_path / "profiles"
    environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)}
    assert (
        _run_profile_script(
            "--scenario", "tiny", "--output-dir", str(output_root), env=environment
        ).returncode
        == 0
    )
    previous = json.loads((output_root / "tiny-manifest.json").read_text())
    before = {name: (output_root / name).read_bytes() for name in previous["artifacts"]}

    failed = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={**environment, "IMMICH_PROFILE_FAIL_DURING_PUBLISH": "1"},
    )

    assert failed.returncode != 0
    assert json.loads((output_root / "tiny-manifest.json").read_text()) == previous
    assert {name: (output_root / name).read_bytes() for name in previous["artifacts"]} == before


def test_profile_replaces_a_scenario_artifact_symlink_without_following_it(tmp_path: Path) -> None:
    """A preexisting artifact symlink must be replaced, never used to write outside the root."""
    output_root = tmp_path / "profiles"
    outside = tmp_path / "outside"
    outside.write_text("keep")
    output_root.mkdir()
    (output_root / "tiny-manifest.json").symlink_to(outside)

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "keep"
    assert not (output_root / "tiny-manifest.json").is_symlink()


def test_profile_replaces_a_generation_artifact_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    """A generation artifact must replace a same-name symlink without touching its target."""
    output_root = tmp_path / "profiles"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    planted = output_root / "tiny-artifact-summary.json"
    planted.symlink_to(outside)

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={
            **os.environ,
            "IMMICH_PROFILE_TEST_ROOT": str(tmp_path),
            "IMMICH_PROFILE_TEST_GENERATION": "artifact",
        },
    )

    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "keep"
    assert not planted.is_symlink()
    assert json.loads(planted.read_text())["scenario"] == "tiny"


def test_profile_manifest_commit_uses_an_exclusive_random_temporary_file(tmp_path: Path) -> None:
    """A guessed legacy commit-temp name must never redirect manifest bytes outside the root."""
    output_root = tmp_path / "profiles"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    predictable_temp = output_root / ".tiny-manifest-secure01.tmp"
    predictable_temp.symlink_to(outside)

    result = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={
            **os.environ,
            "IMMICH_PROFILE_TEST_ROOT": str(tmp_path),
            "IMMICH_PROFILE_TEST_GENERATION": "secure01",
        },
    )

    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "keep"
    assert predictable_temp.is_symlink()


def test_profile_success_removes_only_unreferenced_owned_generation_artifacts(
    tmp_path: Path,
) -> None:
    """A later commit cleans failed Task 7 generations without deleting similar user files."""
    output_root = tmp_path / "profiles"
    environment = {**os.environ, "IMMICH_PROFILE_TEST_ROOT": str(tmp_path)}
    failed = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={
            **environment,
            "IMMICH_PROFILE_TEST_GENERATION": "orphaned",
            "IMMICH_PROFILE_FAIL_DURING_PUBLISH": "1",
        },
    )
    assert failed.returncode != 0
    assert list(output_root.glob("tiny-orphaned-*"))
    user_file = output_root / "tiny-user-not-owned.txt"
    user_file.write_text("keep")

    succeeded = _run_profile_script(
        "--scenario",
        "tiny",
        "--output-dir",
        str(output_root),
        env={**environment, "IMMICH_PROFILE_TEST_GENERATION": "current1"},
    )

    assert succeeded.returncode == 0, succeeded.stderr
    assert not list(output_root.glob("tiny-orphaned-*"))
    assert user_file.read_text() == "keep"
