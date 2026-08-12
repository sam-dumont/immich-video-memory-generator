"""Contracts for benchmark batch creation, publication, and submission."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from tests.integration.assembly.perf_utils import require_benchmark_temporary_root
from tests.performance.test_benchmark_workflow import _uploaded_benchmark_payload

PROJECT_ROOT = Path(__file__).parents[2]
PROFILE_SCRIPT = PROJECT_ROOT / "scripts" / "profile_pipeline.py"


def _fake_recursive_make(tmp_path: Path) -> Path:
    """Create a producer boundary that emits the envelope requested by a recursive Make call."""
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

target = sys.argv[1]
values = dict(argument.split("=", 1) for argument in sys.argv[2:] if "=" in argument)
if os.environ.get("FAKE_BENCHMARK_DELAY_TARGET") == target:
    time.sleep(float(os.environ.get("FAKE_BENCHMARK_DELAY_SECONDS", "0")))
if os.environ.get("FAKE_BENCHMARK_FAIL_TARGET") == target:
    raise SystemExit(42)
revision = values["BENCHMARK_REVISION"]
run = {
    "input_duration_seconds": 1.0,
    "codec": "h264",
    "frame_rate": 24.0,
    "cache_mode": "warm",
    "python_version": "3.12.11",
    "platform": "test-platform",
    "cpu": "test-cpu",
    "git_revision": revision,
}
names = {
    "benchmark-assembly": "benchmark-assembly.json",
    "benchmark-titles-json": "benchmark-titles.json",
    "benchmark-pipeline": "benchmark-pipeline.json",
}
payload = {
    "benchmarks": [{"name": target, "unit": "seconds", "value": 1.0}],
    "results": [{"warmup": {**run, "cache_mode": "cold"}, "repetitions": [run] * 3}],
    "git_revision": revision,
    "source_fingerprint": values["IMMICH_BENCHMARK_BATCH_FINGERPRINT"],
}
if os.environ.get("FAKE_BENCHMARK_INVALID_TARGET") == target:
    payload["results"] = [{}]
pathlib.Path(values["BENCHMARK_OUTPUT_DIR"], names[target]).write_text(json.dumps(payload))
"""
    )
    fake_make.chmod(0o755)
    return fake_make


def _run_benchmark_batch(
    tmp_path: Path,
    target: str,
    *,
    output_root: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    root = output_root or tmp_path / "batches"
    return subprocess.run(
        ["make", target, f"MAKE={_fake_recursive_make(tmp_path)}"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "BENCHMARK_OUTPUT_DIR": str(root),
            "BENCHMARK_REVISION": "revision-under-test",
            "IMMICH_BENCHMARK_TEST_FINGERPRINT": "fingerprint-under-test",
            **(extra_env or {}),
        },
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        ("benchmark-json", {"benchmark-assembly.json", "benchmark-titles.json"}),
        (
            "benchmark-json-full",
            {"benchmark-assembly.json", "benchmark-titles.json", "benchmark-pipeline.json"},
        ),
    ),
)
def test_benchmark_target_publishes_only_a_complete_expected_batch(
    tmp_path: Path, target: str, expected: set[str]
) -> None:
    """The pointer commits only after every target-specific suite has succeeded."""
    root = tmp_path / "batches"

    result = _run_benchmark_batch(tmp_path, target, output_root=root)

    assert result.returncode == 0, result.stderr
    pointer = root / "current-batch"
    assert pointer.is_file() and not pointer.is_symlink()
    batch = Path(pointer.read_text())
    manifest = json.loads((batch / "batch-manifest.json").read_text())
    assert set(manifest["expected"]) == expected
    assert {path.name for path in batch.glob("benchmark-*.json")} == expected
    assert manifest["git_revision"] == "revision-under-test"
    assert manifest["source_fingerprint"] == "fingerprint-under-test"


def test_benchmark_target_rejects_a_symlink_output_root(tmp_path: Path) -> None:
    """Batch creation must never follow a caller-supplied destination symlink."""
    assert require_benchmark_temporary_root(Path("/tmp"), create=False) == Path("/tmp").resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "batches"
    root.symlink_to(outside, target_is_directory=True)

    result = _run_benchmark_batch(tmp_path, "benchmark-json", output_root=root)

    assert result.returncode != 0
    assert not list(outside.iterdir())


@pytest.mark.parametrize("target", ("benchmark-json", "benchmark-submit"))
def test_benchmark_commands_reject_a_repository_output_root(target: str) -> None:
    """Creation and submission share one canonical OS-temporary-root trust boundary."""
    result = subprocess.run(
        ["make", target],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "BENCHMARK_OUTPUT_DIR": str(PROJECT_ROOT),
            "BENCHMARK_REVISION": "revision-under-test",
            "IMMICH_BENCHMARK_TEST_FINGERPRINT": "fingerprint-under-test",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "temporary root" in result.stderr


def test_benchmark_commands_reject_a_symlinked_output_ancestor(tmp_path: Path) -> None:
    """Resolving below a symlink is unsafe even when the final output component is regular."""
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    root = linked / "batches"

    result = _run_benchmark_batch(tmp_path, "benchmark-json", output_root=root)

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not root.exists()


def test_failed_benchmark_batch_never_replaces_the_current_complete_batch(tmp_path: Path) -> None:
    """A producer failure leaves the prior pointer byte-for-byte unchanged."""
    root = tmp_path / "batches"
    first = _run_benchmark_batch(tmp_path, "benchmark-json", output_root=root)
    assert first.returncode == 0, first.stderr
    pointer = root / "current-batch"
    previous_pointer = pointer.read_bytes()

    failed = _run_benchmark_batch(
        tmp_path,
        "benchmark-json",
        output_root=root,
        extra_env={"FAKE_BENCHMARK_FAIL_TARGET": "benchmark-titles-json"},
    )

    assert failed.returncode != 0
    assert pointer.read_bytes() == previous_pointer
    partial = [
        batch for batch in root.glob("batch.*") if not (batch / "batch-manifest.json").exists()
    ]
    assert len(partial) == 1


def test_malformed_successful_producer_never_replaces_the_current_batch(tmp_path: Path) -> None:
    """A zero-exit producer still cannot publish malformed nested measurements."""
    root = tmp_path / "batches"
    first = _run_benchmark_batch(tmp_path, "benchmark-json", output_root=root)
    assert first.returncode == 0, first.stderr
    pointer = root / "current-batch"
    previous_pointer = pointer.read_bytes()

    invalid = _run_benchmark_batch(
        tmp_path,
        "benchmark-json",
        output_root=root,
        extra_env={"FAKE_BENCHMARK_INVALID_TARGET": "benchmark-titles-json"},
    )

    assert invalid.returncode != 0
    assert pointer.read_bytes() == previous_pointer


def test_concurrent_benchmark_batches_finish_independently_and_newer_start_wins(
    tmp_path: Path,
) -> None:
    """Concurrent complete batches coexist, while the newest-started batch owns the pointer."""
    root = tmp_path / "batches"
    fake_make = _fake_recursive_make(tmp_path)
    base_environment = {
        **os.environ,
        "BENCHMARK_OUTPUT_DIR": str(root),
        "BENCHMARK_REVISION": "revision-under-test",
        "IMMICH_BENCHMARK_TEST_FINGERPRINT": "fingerprint-under-test",
    }
    older = subprocess.Popen(
        ["make", "benchmark-json", f"MAKE={fake_make}"],
        cwd=PROJECT_ROOT,
        env={
            **base_environment,
            "FAKE_BENCHMARK_DELAY_TARGET": "benchmark-assembly",
            "FAKE_BENCHMARK_DELAY_SECONDS": "0.6",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.15)
    newer = subprocess.run(
        ["make", "benchmark-json", f"MAKE={fake_make}"],
        cwd=PROJECT_ROOT,
        env=base_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    older_stdout, older_stderr = older.communicate(timeout=10)

    assert newer.returncode == 0, newer.stderr
    assert older.returncode == 0, f"{older_stdout}\n{older_stderr}"
    batches = list(root.glob("batch.*"))
    assert len(batches) == 2
    manifests = {
        batch: json.loads((batch / "batch-manifest.json").read_text()) for batch in batches
    }
    assert all(
        {path.name for path in batch.glob("benchmark-*.json")}
        == {"benchmark-assembly.json", "benchmark-titles.json"}
        for batch in batches
    )
    current = Path((root / "current-batch").read_text())
    assert manifests[current]["started_ns"] == max(
        manifest["started_ns"] for manifest in manifests.values()
    )


def test_benchmark_publication_replaces_an_unsafe_existing_pointer(tmp_path: Path) -> None:
    """Publication never trusts start metadata reached through a path outside its batch root."""
    root = tmp_path / "batches"
    root.mkdir()
    outside = tmp_path / "outside-batch"
    outside.mkdir()
    sentinel = outside / "batch-manifest.json"
    sentinel.write_text(json.dumps({"started_ns": 10**30}))
    pointer = root / "current-batch"
    pointer.write_text(str(outside))

    result = _run_benchmark_batch(tmp_path, "benchmark-json", output_root=root)

    assert result.returncode == 0, result.stderr
    current = Path(pointer.read_text())
    assert current.parent == root
    assert current.name.startswith("batch.")
    assert sentinel.read_text() == json.dumps({"started_ns": 10**30})


def _submit_payload(
    *,
    revision: str,
    measurement_revision: str,
    fingerprint: str = "fingerprint-under-test",
    valid: bool = True,
) -> dict[str, object]:
    """Build a dispatchable envelope with a separately controlled source identity."""
    payload = _uploaded_benchmark_payload()
    payload["git_revision"] = revision
    payload["source_fingerprint"] = fingerprint
    if not valid:
        payload["results"] = []
        return payload
    for run in [payload["results"][0]["warmup"], *payload["results"][0]["repetitions"]]:  # type: ignore[index]
        run["git_revision"] = measurement_revision  # type: ignore[index]
    return payload


def _run_benchmark_submit(
    tmp_path: Path,
    payload: dict[str, object] | None,
    *,
    gh_exit: int = 0,
    omit_title: bool = False,
    symlink_kind: str | None = None,
    unsafe_pointer: bool = False,
    title_payload: dict[str, object] | None = None,
    full_batch: bool = False,
    current_fingerprint: str = "fingerprint-under-test",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the real Make target with a fake gh executable and an isolated envelope root."""
    root = tmp_path / "envelopes"
    storage_root = tmp_path / "envelopes-real" if symlink_kind == "root" else root
    storage_root.mkdir()
    if symlink_kind == "root":
        root.symlink_to(storage_root, target_is_directory=True)
    batch = tmp_path / "outside-batch" if unsafe_pointer else storage_root / "batch.00000001"
    batch.mkdir()
    if unsafe_pointer:
        (root / "batch.fake").mkdir()
    if payload is not None:
        (batch / "benchmark-assembly.json").write_text(json.dumps(payload))
        if not omit_title:
            (batch / "benchmark-titles.json").write_text(
                json.dumps(payload if title_payload is None else title_payload)
            )
        if full_batch:
            (batch / "benchmark-pipeline.json").write_text(json.dumps(payload))
        (batch / "batch-manifest.json").write_text(
            json.dumps(
                {
                    "expected": [
                        "benchmark-assembly.json",
                        *(["benchmark-pipeline.json"] if full_batch else []),
                        "benchmark-titles.json",
                    ],
                    "git_revision": "revision-under-test",
                    "source_fingerprint": current_fingerprint,
                }
            )
        )
    if symlink_kind == "manifest":
        outside_manifest = tmp_path / "outside-manifest.json"
        (batch / "batch-manifest.json").replace(outside_manifest)
        (batch / "batch-manifest.json").symlink_to(outside_manifest)
    if symlink_kind == "artifact":
        outside_artifact = tmp_path / "outside-artifact.json"
        (batch / "benchmark-titles.json").replace(outside_artifact)
        (batch / "benchmark-titles.json").symlink_to(outside_artifact)
    current_batch = storage_root / "current-batch"
    if unsafe_pointer:
        batch_reference = root / "batch.fake" / ".." / ".." / batch.name
    else:
        batch_reference = root / batch.name if symlink_kind == "root" else batch
    current_batch.write_text(str(batch_reference))
    if symlink_kind == "pointer":
        outside_pointer = tmp_path / "outside-pointer"
        current_batch.replace(outside_pointer)
        current_batch.symlink_to(outside_pointer)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "gh-calls"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$GH_CALLS"\nexit "${GH_EXIT:-0}"\n')
    fake_gh.chmod(0o755)
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROFILE_SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result = subprocess.run(
        ["make", "benchmark-submit"],
        cwd=PROFILE_SCRIPT.parents[1],
        env={
            **os.environ,
            "BENCHMARK_OUTPUT_DIR": str(root),
            "BENCHMARK_REVISION": "revision-under-test",
            "IMMICH_BENCHMARK_TEST_FINGERPRINT": current_fingerprint,
            "GH_CALLS": str(calls),
            "GH_EXIT": str(gh_exit),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert current_revision
    return result, calls


@pytest.mark.parametrize("kind", ("invalid", "missing", "stale"))
def test_benchmark_submit_rejects_bad_envelopes_without_dispatch(tmp_path: Path, kind: str) -> None:
    """Validation failure must terminate the shell before fake gh can observe a dispatch."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROFILE_SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    payload = {
        "invalid": _submit_payload(
            revision="revision-under-test", measurement_revision=current_revision, valid=False
        ),
        "missing": None,
        "stale": _submit_payload(revision="stale", measurement_revision=current_revision),
    }[kind]

    result, calls = _run_benchmark_submit(tmp_path, payload)

    assert result.returncode != 0
    assert not calls.exists()


def test_benchmark_submit_dispatches_one_current_envelope_once(tmp_path: Path) -> None:
    """A current normal batch reaches one merged dispatch boundary."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROFILE_SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
    )

    assert result.returncode == 0, result.stderr
    dispatched = calls.read_text().splitlines()
    assert len(dispatched) == 1
    assert "api repos/:owner/:repo/actions/workflows/benchmark.yml/dispatches" in dispatched[0]
    assert "inputs[suite]=all" in dispatched[0]
    assert dispatched[0].count('"name":"controlled"') == 2


def test_benchmark_submit_dispatches_every_full_batch_manifest_suite(tmp_path: Path) -> None:
    """A full current batch merges assembly, pipeline, and titles into one dispatch."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        full_batch=True,
    )

    assert result.returncode == 0, result.stderr
    dispatched = calls.read_text().splitlines()
    assert len(dispatched) == 1
    assert "inputs[suite]=all" in dispatched[0]
    assert dispatched[0].count('"benchmarks"') == 1
    assert dispatched[0].count('"results"') == 1
    assert dispatched[0].count('"name":"controlled"') == 3


def test_benchmark_workflow_accepts_pipeline_as_a_dispatch_suite() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "benchmark.yml").read_text()
    options = workflow.split("        options:\n", 1)[1].split("      sha:\n", 1)[0]

    assert "          - pipeline\n" in options


@pytest.mark.parametrize("extra_name", ("benchmark-stale.json", "stale-artifact.txt"))
def test_benchmark_submit_rejects_stale_extra_artifacts_before_dispatch(
    tmp_path: Path, extra_name: str
) -> None:
    """Only the manifest-owned envelope set can cross the dispatch boundary."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROFILE_SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    payload = _submit_payload(revision="revision-under-test", measurement_revision=current_revision)
    root = tmp_path / "envelopes"
    batch = root / "batch.00000001"
    root.mkdir()
    batch.mkdir()
    for name in ("benchmark-assembly.json", "benchmark-titles.json", extra_name):
        (batch / name).write_text(json.dumps(payload))
    (batch / "batch-manifest.json").write_text(
        json.dumps(
            {
                "expected": ["benchmark-assembly.json", "benchmark-titles.json"],
                "git_revision": "revision-under-test",
                "source_fingerprint": "fingerprint-under-test",
            }
        )
    )
    (root / "current-batch").write_text(str(batch))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "gh-calls"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$GH_CALLS"\n')
    fake_gh.chmod(0o755)

    result = subprocess.run(
        ["make", "benchmark-submit"],
        cwd=PROFILE_SCRIPT.parents[1],
        env={
            **os.environ,
            "BENCHMARK_OUTPUT_DIR": str(root),
            "BENCHMARK_REVISION": "revision-under-test",
            "IMMICH_BENCHMARK_TEST_FINGERPRINT": "fingerprint-under-test",
            "GH_CALLS": str(calls),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not calls.exists()


def test_benchmark_submit_propagates_a_dispatch_failure(tmp_path: Path) -> None:
    """A failing gh process must not be hidden by the success echo after it."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROFILE_SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        gh_exit=23,
    )

    assert result.returncode != 0
    assert len(calls.read_text().splitlines()) == 1


def test_benchmark_submit_rejects_a_partial_batch_before_dispatch(tmp_path: Path) -> None:
    """Every manifest suite must validate before the first dispatch can occur."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        omit_title=True,
    )

    assert result.returncode != 0
    assert not calls.exists()


def test_benchmark_submit_validates_every_envelope_before_first_dispatch(tmp_path: Path) -> None:
    """A valid first suite cannot dispatch before a corrupt later suite is rejected."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        title_payload=_submit_payload(
            revision="revision-under-test",
            measurement_revision=current_revision,
            valid=False,
        ),
    )

    assert result.returncode != 0
    assert not calls.exists()


def test_benchmark_submit_ignores_public_fingerprint_override_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    """A caller cannot mask changed source bytes with BENCHMARK_FINGERPRINT."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    payload = _submit_payload(
        revision="revision-under-test",
        measurement_revision=current_revision,
        fingerprint="old-fingerprint",
    )
    result, calls = _run_benchmark_submit(tmp_path, payload, current_fingerprint="old-fingerprint")
    environment = {
        **os.environ,
        "BENCHMARK_FINGERPRINT": "old-fingerprint",
        "IMMICH_BENCHMARK_TEST_FINGERPRINT": "mutated-fingerprint",
    }
    # The helper's first run establishes that the old payload itself is structurally valid.
    assert result.returncode == 0, result.stderr
    root = tmp_path / "envelopes"
    fake_bin = tmp_path / "bin"
    calls.unlink()
    mutated = subprocess.run(
        ["make", "benchmark-submit"],
        cwd=PROJECT_ROOT,
        env={
            **environment,
            "BENCHMARK_OUTPUT_DIR": str(root),
            "BENCHMARK_REVISION": "revision-under-test",
            "GH_CALLS": str(calls),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert mutated.returncode != 0
    assert not calls.exists()

    indeterminate_root = tmp_path / "indeterminate-batches"
    indeterminate_create = _run_benchmark_batch(
        tmp_path,
        "benchmark-json",
        output_root=indeterminate_root,
        extra_env={"IMMICH_BENCHMARK_TEST_FINGERPRINT": "unknown"},
    )
    assert indeterminate_create.returncode != 0
    assert not (indeterminate_root / "current-batch").exists()

    indeterminate_submit = subprocess.run(
        ["make", "benchmark-submit"],
        cwd=PROJECT_ROOT,
        env={
            **environment,
            "BENCHMARK_OUTPUT_DIR": str(root),
            "BENCHMARK_REVISION": "revision-under-test",
            "IMMICH_BENCHMARK_TEST_FINGERPRINT": "unknown",
            "GH_CALLS": str(calls),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert indeterminate_submit.returncode != 0
    assert not calls.exists()


@pytest.mark.parametrize("symlink_kind", ("root", "pointer", "manifest", "artifact"))
def test_benchmark_submit_rejects_symlinks_at_every_trust_boundary(
    tmp_path: Path, symlink_kind: str
) -> None:
    """Submission never follows a root, pointer, manifest, or suite-artifact symlink."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        symlink_kind=symlink_kind,
    )

    assert result.returncode != 0
    assert not calls.exists()


def test_benchmark_submit_rejects_a_traversal_pointer_before_dispatch(tmp_path: Path) -> None:
    """A textual batch.* prefix cannot authorize a directory outside the batch root."""
    current_revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result, calls = _run_benchmark_submit(
        tmp_path,
        _submit_payload(revision="revision-under-test", measurement_revision=current_revision),
        unsafe_pointer=True,
    )

    assert result.returncode != 0
    assert not calls.exists()
