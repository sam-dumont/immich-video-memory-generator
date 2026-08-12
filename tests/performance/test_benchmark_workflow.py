"""Contracts for benchmark batches, submission, and CI workflow policy."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
PROFILE_SCRIPT = PROJECT_ROOT / "scripts" / "profile_pipeline.py"


def _run_uploaded_benchmark_validator(
    tmp_path: Path,
    payload: object,
    *,
    step_name: str = "Validate uploaded benchmark reproduction data",
) -> subprocess.CompletedProcess[str]:
    """Execute the exact Python validator embedded in the workflow."""
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "benchmark.yml").read_text()
    step = workflow.split(f"      - name: {step_name}\n", maxsplit=1)[1]
    program = textwrap.dedent(
        step.split("          python3 - <<'PY'\n", maxsplit=1)[1].split(
            "          PY\n", maxsplit=1
        )[0]
    )
    results_path = tmp_path / "tests" / "benchmark-upload.json"
    results_path.parent.mkdir()
    results_path.write_text(json.dumps(payload))
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _comparable_baseline_counter_programs() -> list[str]:
    """Extract both Linux and dispatch-ingest counter implementations from the workflow."""
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "benchmark.yml").read_text()
    steps = workflow.split("      - name: Count comparable benchmark baselines\n")[1:]
    return [
        textwrap.dedent(
            step.split("          python3 - <<'PY'\n", maxsplit=1)[1].split(
                "          PY\n", maxsplit=1
            )[0]
        )
        for step in steps
    ]


def _uploaded_benchmark_payload(*, repetitions: int = 3) -> dict[str, object]:
    reproduction = {
        "input_duration_seconds": 1.0,
        "codec": "h264",
        "frame_rate": 24.0,
        "cache_mode": "warm",
        "python_version": "3.12.11",
        "platform": "test-platform",
        "cpu": "test-cpu",
        "git_revision": "abc1234",
    }
    return {
        "benchmarks": [{"name": "controlled", "unit": "seconds", "value": 1.0}],
        "results": [
            {
                "warmup": {**reproduction, "cache_mode": "cold"},
                "repetitions": [{**reproduction} for _ in range(repetitions)],
            }
        ],
    }


@pytest.mark.parametrize(
    "step_name",
    (
        "Validate merged benchmark reproduction data",
        "Validate uploaded benchmark reproduction data",
    ),
)
def test_benchmark_workflow_validates_each_enriched_upload_before_projecting_metrics(
    tmp_path: Path, step_name: str
) -> None:
    """A valid detailed upload must be accepted and yield action-format metric JSON."""
    result = _run_uploaded_benchmark_validator(
        tmp_path, _uploaded_benchmark_payload(), step_name=step_name
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "tests" / "benchmark-results.json").read_text()) == [
        {"name": "controlled", "unit": "seconds", "value": 1.0}
    ]


def test_benchmark_workflow_rejects_missing_reproduction_fields(tmp_path: Path) -> None:
    """Removing a reproduction value must reject an otherwise valid upload."""
    payload = _uploaded_benchmark_payload()
    first_repetition = payload["results"][0]["repetitions"][0]  # type: ignore[index]
    first_repetition.pop("git_revision")  # type: ignore[union-attr]

    result = _run_uploaded_benchmark_validator(tmp_path, payload)

    assert result.returncode != 0
    assert "lacks reproduction fields: git_revision" in result.stderr


def test_benchmark_workflow_rejects_fewer_than_three_repetitions(tmp_path: Path) -> None:
    """Reducing the sample to two must reject it before benchmark-action sees it."""
    result = _run_uploaded_benchmark_validator(tmp_path, _uploaded_benchmark_payload(repetitions=2))

    assert result.returncode != 0
    assert "requires at least three measured repetitions" in result.stderr


@pytest.mark.parametrize("mutation", ("missing-field", "two-repetitions"))
def test_benchmark_workflow_merged_envelope_rejects_invalid_nested_measurements(
    tmp_path: Path, mutation: str
) -> None:
    """The CI merge path must validate nested samples before projecting action metrics."""
    payload = _uploaded_benchmark_payload(repetitions=2 if mutation == "two-repetitions" else 3)
    if mutation == "missing-field":
        payload["results"][0]["repetitions"][0].pop("cpu")  # type: ignore[index,union-attr]

    result = _run_uploaded_benchmark_validator(
        tmp_path, payload, step_name="Validate merged benchmark reproduction data"
    )

    assert result.returncode != 0
    assert (
        "reproduction fields" in result.stderr
        or "at least three measured repetitions" in result.stderr
    )


def test_benchmark_workflow_keeps_comparison_alerts_advisory() -> None:
    """A fixed false alert gate would ignore the tenth comparable baseline."""
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "benchmark.yml").read_text()

    expression = "fail-on-alert: ${{ fromJSON(env.BENCHMARK_COMPARABLE_BASELINES) >= 10 }}"
    assert workflow.count("BENCHMARK_COMPARABLE_BASELINES") >= 4
    assert workflow.count(expression) == 2


def test_benchmark_workflow_correctness_runs_block_and_cache_precedes_each_count() -> None:
    """Correctness failures stop CI, while both history gates count only restored action data."""
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "benchmark.yml").read_text()
    assembly_step = workflow.split("      - name: Run assembly benchmarks\n", 1)[1].split(
        "      - name: Run title benchmarks\n", 1
    )[0]
    title_step = workflow.split("      - name: Run title benchmarks\n", 1)[1].split(
        "      - name: Merge benchmark JSON files\n", 1
    )[0]
    assert "continue-on-error" not in assembly_step
    assert "continue-on-error" not in title_step
    assert (
        workflow.count("uses: actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830") == 2
    )
    for job in (
        workflow.split("  bench-linux:\n", 1)[1].split("  ingest:\n", 1)[0],
        workflow.split("  ingest:\n", 1)[1],
    ):
        assert job.index("Restore benchmark cache") < job.index(
            "Count comparable benchmark baselines"
        )


def test_benchmark_targets_use_an_explicit_temporary_output_root() -> None:
    """A target must not recreate tracked JSON beneath tests after a clean checkout."""
    makefile = (Path(__file__).parents[2] / "Makefile").read_text()
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "benchmark.yml").read_text()

    assert "BENCHMARK_OUTPUT_DIR" in makefile
    assert (
        "tests/benchmark-assembly.json"
        not in workflow.split("Run assembly benchmarks", 1)[1].split("Run title benchmarks", 1)[0]
    )
    assert 'echo "BENCHMARK_OUTPUT_DIR=$RUNNER_TEMP/immich-memories-benchmarks"' in workflow
    assert "BENCHMARK_OUTPUT_DIR: ${{ runner.temp }}" not in workflow


@pytest.mark.parametrize(("history_size", "expected"), ((0, 0), (9, 9), (10, 10)))
def test_benchmark_workflow_counts_comparable_history_before_enabling_alerts(
    tmp_path: Path, history_size: int, expected: int
) -> None:
    """Counting entries after a fresh cache restore prevents a permanently advisory gate."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "benchmark-data.json").write_text(
        json.dumps(
            {
                "lastUpdate": 0,
                "repoUrl": "test",
                "entries": {"Benchmark": [{"benches": []}] * history_size},
            }
        )
    )
    environment_path = tmp_path / "github-env"
    programs = _comparable_baseline_counter_programs()
    assert len(programs) == 2
    for program in programs:
        environment_path.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env={**os.environ, "GITHUB_ENV": str(environment_path)},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert environment_path.read_text() == f"BENCHMARK_COMPARABLE_BASELINES={expected}\n"


@pytest.mark.parametrize(("benchmark_records", "expected"), ((9, 9), (10, 10)))
def test_benchmark_workflow_counts_only_valid_benchmark_history_records(
    tmp_path: Path, benchmark_records: int, expected: int
) -> None:
    """Other action names and malformed records cannot make this runner prematurely blocking."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "benchmark-data.json").write_text(
        json.dumps(
            {
                "lastUpdate": 0,
                "repoUrl": "test",
                "entries": {
                    "Benchmark": [{"benches": []} for _ in range(benchmark_records)] + [{}],
                    "unrelated": [{"benches": []} for _ in range(12)],
                },
            }
        )
    )
    environment_path = tmp_path / "github-env"
    for program in _comparable_baseline_counter_programs():
        environment_path.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env={**os.environ, "GITHUB_ENV": str(environment_path)},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert environment_path.read_text() == f"BENCHMARK_COMPARABLE_BASELINES={expected}\n"


def test_benchmark_output_root_is_set_after_the_runner_starts() -> None:
    """Runner temp is a runtime shell value, never a workflow/job expression context."""
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "benchmark.yml").read_text()

    assert "${{ runner.temp }}" not in workflow
    assert workflow.index("Set benchmark output root") < workflow.index("Run assembly benchmarks")
