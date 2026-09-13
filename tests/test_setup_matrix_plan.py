"""The setup matrix renders the same plan every time, and never a secret.

The dry run IS the acceptance test for the runner: the ten cells are never run in
CI, so the only thing that can be asserted is that the plan they would run is the
right one, and that it can be read out loud without leaking a host or a key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_plan import (  # noqa: E402
    PlanError,
    build_plan,
    dry_run_text,
    inference_overlay_steps,
    load_manifest,
    read_cells,
)

FULL_ENV = {
    "MATRIX_OMLX_BASE_URL": "http://omlx.invalid:8000/v1",
    "OPENAI_API_KEY": "secret-omlx-key",
    "MATRIX_NAS_SSH": "someone@a-nas.invalid",
    "MATRIX_NAS_DOCKER": "/usr/local/bin/docker",
    "MATRIX_NAS_CACHE": "/nowhere/models",
    "MATRIX_NAS_OUT": "/nowhere/matrix",
    "MATRIX_INFERENCE_BASE_URL": "http://inference.invalid:8092",
    "MATRIX_K8S_CONTEXT": "a-cluster-context",
    "MATRIX_K8S_NAMESPACE": "private-namespace",
    "MELIOUS_AI_BASE_URL": "https://hosted.invalid/v1",
    "MELIOUS_AI_KEY": "secret-melious-key",
    "ZAI_BASE_URL": "https://zai.invalid/v4",
    "ZAI_API_KEY": "secret-zai-key",
    "MATRIX_FIXTURE_BASE_URL": "http://a-fixture.invalid:8078",
}


@pytest.fixture
def manifest() -> dict:
    return load_manifest()


def _plan(manifest: dict, tmp_path: Path, environment: dict, **overrides):
    return build_plan(
        manifest=manifest,
        library=overrides.get("library", "demo"),
        month=overrides.get("month"),
        lanes=overrides.get("lanes", ()),
        cell_ids=overrides.get("cell_ids", ()),
        out_dir=tmp_path,
        image="ghcr.io/example/app:0.84.1",
        environment=environment,
    )


def test_the_manifest_holds_the_ten_setups(manifest: dict) -> None:
    cells = read_cells(manifest)
    assert len(cells) == 10
    assert {cell.lane for cell in cells} == {"mac", "nas", "k8s"}
    assert [cell.id for cell in cells][0] == "mac-local", "the reference cut must come first"


def test_a_full_environment_leaves_nothing_skipped(manifest: dict, tmp_path: Path) -> None:
    plan = _plan(manifest, tmp_path, FULL_ENV)
    assert plan.skipped == ()


def test_a_missing_variable_keeps_the_cell_with_a_reason(manifest: dict, tmp_path: Path) -> None:
    without_nas = {key: value for key, value in FULL_ENV.items() if key != "MATRIX_NAS_SSH"}
    plan = _plan(manifest, tmp_path, without_nas)
    skipped = {item.cell.id: item.skip_reason for item in plan.skipped}
    assert set(skipped) == {
        "nas-rules-local",
        "nas-rules-service",
        "nas-hosted-melious",
        "nas-hosted-zai",
    }
    assert "MATRIX_NAS_SSH" in skipped["nas-rules-local"]


def test_no_environment_value_reaches_the_rendered_plan(manifest: dict, tmp_path: Path) -> None:
    """The transcript goes in a pull request, so it must carry names, not values."""
    text = dry_run_text(
        _plan(manifest, tmp_path, FULL_ENV),
        overlay=inference_overlay_steps(device="auto", keep=False),
    )
    leaked = [value for value in FULL_ENV.values() if value in text]
    assert leaked == []
    assert "$MATRIX_NAS_SSH" in text
    assert "$MATRIX_K8S_NAMESPACE" in text


def test_the_dry_run_is_byte_identical_between_calls(manifest: dict, tmp_path: Path) -> None:
    first = dry_run_text(_plan(manifest, tmp_path, FULL_ENV))
    second = dry_run_text(_plan(manifest, tmp_path, FULL_ENV))
    assert first == second


def test_service_cells_refuse_a_silent_local_fallback(manifest: dict, tmp_path: Path) -> None:
    """With the fallback on, an unreachable service reports a time for a setup that never ran."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    service_cells = [item for item in plan.cells if item.cell.facts == "service"]
    assert service_cells
    for item in service_cells:
        assert item.pins["inference.fallback_to_local"] is False
        assert item.pins["inference.facts_base_url"]


def test_local_facts_cells_ask_for_no_service(manifest: dict, tmp_path: Path) -> None:
    plan = _plan(manifest, tmp_path, FULL_ENV)
    for item in plan.cells:
        if item.cell.facts == "local":
            assert item.pins["inference.facts_base_url"] == ""


def test_every_cell_pins_the_attempt_directory_to_its_own_id(
    manifest: dict, tmp_path: Path
) -> None:
    """Without --memory-key the attempt directory is named after the output file."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    for item in plan.cells:
        rendered = " ".join(str(step) for step in item.steps) + " ".join(item.manifests.values())
        assert f"--memory-key {item.cell.id}" in rendered


def test_credentials_are_named_never_written(manifest: dict, tmp_path: Path) -> None:
    """A key reaches the app through the process env; the config file holds the reference."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    zai = next(item for item in plan.cells if item.cell.id == "nas-hosted-zai")
    assert zai.app_credentials == ("ZAI_API_KEY",)
    assert "${ZAI_API_KEY}" in zai.config_yaml
    assert FULL_ENV["ZAI_API_KEY"] not in zai.config_yaml
    assert "-e ZAI_API_KEY" in str(next(step for step in zai.steps if step.name == "run"))


def test_the_pinned_config_nests_the_way_the_loader_reads_it(
    manifest: dict, tmp_path: Path
) -> None:
    item = next(
        cell for cell in _plan(manifest, tmp_path, FULL_ENV).cells if cell.cell.id == "mac-local"
    )
    config = yaml.safe_load(item.config_yaml)
    assert config["editorial"]["preparation"]["tier"] == "full"
    assert config["editorial"]["reader"] == "model"
    assert config["llm"]["base_url"] == "$MATRIX_OMLX_BASE_URL"


def test_the_nas_lane_caps_the_container_at_a_nas(manifest: dict, tmp_path: Path) -> None:
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    run = str(next(step for step in item.steps if step.name == "run"))
    assert "--cpus 4" in run
    assert "--memory 4g" in run


def test_the_cluster_lane_renders_a_configmap_and_a_job(manifest: dict, tmp_path: Path) -> None:
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "k8s-rules-service"
    )
    job = yaml.safe_load(item.manifests["job.yaml"])
    config_map = yaml.safe_load(item.manifests["configmap.yaml"])
    assert job["kind"] == "Job"
    assert job["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
    assert (
        yaml.safe_load(config_map["data"]["config.yaml"])["inference"]["fallback_to_local"] is False
    )


def test_the_overlay_comes_down_unless_it_is_kept() -> None:
    torn_down = [step.name for step in inference_overlay_steps(device="cpu", keep=False)]
    kept = [step.name for step in inference_overlay_steps(device="cpu", keep=True)]
    assert "delete-inference" in torn_down
    assert "delete-inference" not in kept


def test_a_private_library_is_marked_as_needing_anonymising(manifest: dict, tmp_path: Path) -> None:
    assert _plan(manifest, tmp_path, FULL_ENV, library="february").anonymize_required is True
    assert _plan(manifest, tmp_path, FULL_ENV).anonymize_required is False


def test_an_unknown_cell_is_an_error_not_an_empty_run(manifest: dict, tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="unknown cell"):
        _plan(manifest, tmp_path, FULL_ENV, cell_ids=("nas-rules-lokal",))


def test_a_malformed_month_is_refused(manifest: dict, tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="YYYY-MM"):
        _plan(manifest, tmp_path, FULL_ENV, month="2024-13")


def test_a_cluster_cell_copies_its_results_through_a_collector(
    manifest: dict, tmp_path: Path
) -> None:
    """`kubectl cp` shells into the pod to run tar, and a finished Job has no pod to shell into."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "k8s-rules-local"
    )
    names = [step.name for step in item.steps]
    assert names.index("wait") < names.index("apply-collector") < names.index("copy-out")
    assert "delete-collector" in names
    assert yaml.safe_load(item.manifests["collector.yaml"])["kind"] == "Pod"


def test_a_cluster_secret_is_created_from_the_environment_not_a_file(
    manifest: dict, tmp_path: Path
) -> None:
    plan = _plan(manifest, tmp_path, FULL_ENV)
    hosted = next(item for item in plan.cells if item.cell.id == "k8s-hosted-zai")
    rules = next(item for item in plan.cells if item.cell.id == "k8s-rules-local")
    create = str(next(step for step in hosted.steps if step.name == "make-secret"))
    assert "--from-literal=ZAI_API_KEY=$ZAI_API_KEY" in create
    assert FULL_ENV["ZAI_API_KEY"] not in create
    assert all(name not in str(hosted.manifests) for name in [FULL_ENV["ZAI_API_KEY"]])
    # A cell with no credential gets no secret and no envFrom to dangle on.
    assert [step.name for step in rules.steps if "secret" in step.name] == []
    assert "secretRef" not in rules.manifests["job.yaml"]


def test_the_nas_script_reaches_the_remote_shell_as_one_argument(
    manifest: dict, tmp_path: Path
) -> None:
    """ssh concatenates its arguments and lets the remote shell re-split them."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    run = next(step for step in item.steps if step.name == "run")
    assert len(run.command) == 3, "ssh, the destination, and one command string"
    assert run.command[2].startswith("$MATRIX_NAS_DOCKER run")
    assert "/bin/bash -lc '" in run.command[2]


def test_a_printed_step_is_a_line_a_shell_could_actually_run(
    manifest: dict, tmp_path: Path
) -> None:
    """Single quotes do not nest, and the NAS step carries a quoted script inside its argument."""
    import shlex

    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    run = next(step for step in item.steps if step.name == "run")
    assert shlex.split(str(run)) == list(run.command)
