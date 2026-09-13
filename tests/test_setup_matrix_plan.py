"""The setup matrix renders the same plan every time, and never a secret.

The dry run IS the acceptance test for the runner: the ten cells are never run in
CI, so the only thing that can be asserted is that the plan they would run is the
right one, and that it can be read out loud without leaking a host or a key.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from setup_matrix_plan import (  # noqa: E402
    DERIVED_ADDRESS,
    INFERENCE_ENV,
    INFERENCE_IMAGE,
    LAN_OVERLAY,
    PlanError,
    build_plan,
    dry_run_text,
    inference_image,
    inference_overlay_steps,
    load_manifest,
    needs_lan_address,
    read_cells,
    retag_inference,
)

FULL_ENV = {
    "MATRIX_OMLX_BASE_URL": "http://omlx.invalid:8000/v1",
    "MATRIX_CAPTION_BASE_URL": "http://captions.invalid:8092/v1",
    "OPENAI_API_KEY": "secret-omlx-key",
    "MATRIX_NAS_SSH": "someone@a-nas.invalid",
    "MATRIX_NAS_DOCKER": "/usr/local/bin/docker",
    "MATRIX_NAS_CACHE": "/nowhere/models",
    "MATRIX_NAS_OUT": "/nowhere/matrix",
    "MATRIX_K8S_CONTEXT": "a-cluster-context",
    "MATRIX_K8S_NAMESPACE": "private-namespace",
    "MELIOUS_AI_BASE_URL": "https://hosted.invalid/v1",
    "MELIOUS_AI_KEY": "secret-melious-key",
    "ZAI_BASE_URL": "https://zai.invalid/v4",
    "ZAI_API_KEY": "secret-zai-key",
    "MATRIX_FIXTURE_BASE_URL": "http://a-fixture.invalid:8078",
    # Supplied by the runner, never by the operator: it is read off the
    # LoadBalancer. Present here because build_plan is given what the runner
    # would have put in the environment by then.
    INFERENCE_ENV: DERIVED_ADDRESS,
}


TAG = "0.87.4"


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


def test_the_nas_service_cells_need_no_hand_set_inference_address(
    manifest: dict, tmp_path: Path
) -> None:
    """The runner derives it from the LoadBalancer, so nobody has to look one up."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    for item in plan.cells:
        assert INFERENCE_ENV not in item.cell.requires_env, item.cell.id
    assert plan.skipped == ()


def test_only_the_nas_lane_needs_an_address_off_the_cluster(manifest: dict) -> None:
    """A cluster cell reaches the service over the cluster's own DNS."""
    cells = read_cells(manifest)
    assert needs_lan_address(cells) is True
    assert needs_lan_address(tuple(c for c in cells if c.lane != "nas")) is False
    for cell in cells:
        if cell.lane == "k8s" and cell.facts == "service":
            assert cell.config["inference.facts_base_url"] == "http://inference:8092"


def test_the_load_balancer_comes_up_before_the_address_is_read_and_goes_away_after() -> None:
    overlay = inference_overlay_steps(device="cpu", keep=False, lan=True, tag=TAG)
    steps = [step.name for step in overlay]
    assert steps.index("apply-inference-lan") < steps.index("read-lan-address")
    assert "delete-inference-lan" in steps
    assert steps.index("delete-inference-lan") < steps.index("delete-inference")
    assert LAN_OVERLAY in str(next(s for s in overlay if s.name == "apply-inference-lan"))


def test_the_service_runs_the_release_the_cells_run_not_the_committed_pin() -> None:
    """The overlays pin a release of their own, and the cluster lane ran two behind it.

    The rendered YAML is rewritten rather than the overlay file, so what is
    committed stays what a reader applies by hand.
    """
    rendered = (
        "    spec:\n"
        "      containers:\n"
        "        - name: inference\n"
        f"          image: {INFERENCE_IMAGE}:0.85.0-cuda\n"
        "          ports:\n"
    )
    retagged = retag_inference(rendered, inference_image(TAG, device="cuda"))
    assert f"image: {INFERENCE_IMAGE}:{TAG}-cuda\n" in retagged
    assert "0.85.0" not in retagged
    assert inference_image(TAG, device="cpu") == f"{INFERENCE_IMAGE}:{TAG}"


def test_nothing_but_the_inference_image_is_rewritten() -> None:
    """A rendered overlay is applied whole, so the rewrite has to be the one line."""
    app = "          image: ghcr.io/sam-dumont/immich-video-memory-generator:0.84.1\n"
    assert retag_inference(app, inference_image(TAG, device="cpu")) == app


def test_the_dry_run_names_the_tag_the_service_will_run() -> None:
    steps = inference_overlay_steps(device="cpu", keep=False, lan=False, tag=TAG)
    assert f"{INFERENCE_IMAGE}:{TAG}" in " ".join(str(step) for step in steps)
    applied = next(step for step in steps if step.name == "apply-inference")
    assert applied.command[:2] == ("kubectl", "kustomize"), "rendered, then rewritten, then applied"
    assert applied.pipe_to[-3:] == ("apply", "-f", "-")


def test_no_load_balancer_is_asked_for_when_nothing_outside_the_cluster_calls() -> None:
    steps = [s.name for s in inference_overlay_steps(device="cpu", keep=False, lan=False, tag=TAG)]
    assert not [name for name in steps if "lan" in name]


def test_the_dry_run_says_the_address_is_derived_rather_than_inventing_one(
    manifest: dict, tmp_path: Path
) -> None:
    text = dry_run_text(
        _plan(manifest, tmp_path, FULL_ENV),
        overlay=inference_overlay_steps(device="auto", keep=False, lan=True, tag=TAG),
    )
    assert f"{INFERENCE_ENV}={DERIVED_ADDRESS}" in text


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
        overlay=inference_overlay_steps(device="auto", keep=False, lan=True, tag=TAG),
    )
    # The derived-address placeholder is the one value meant to be printed: it
    # names where the address comes from instead of naming an address.
    leaked = [value for value in FULL_ENV.values() if value in text and value != DERIVED_ADDRESS]
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


def test_no_two_cells_write_into_the_same_editorial_cache(manifest: dict, tmp_path: Path) -> None:
    """The bank remembers verdicts, so a shared cache hands one cell another's judgement.

    The first real Mac run proved it: `mac-rules` reported losses in the model's
    words because `mac-local` had just filled the bank they share.
    """
    plan = _plan(manifest, tmp_path, FULL_ENV)
    for item in plan.cells:
        cache = item.pins["cache.directory"]
        assert item.cache_dir == cache
        assert item.pins["cache.database"].startswith(cache)
        # Blank is what resolves the annotation bank under the cache directory;
        # an operator's own override would put every cell back in one bank.
        assert item.pins["editorial.annotation_database"] == ""
    # The remote lanes reach their own cache at one container path, so it is the
    # mount behind it that has to differ; each lane's own test asserts that.
    mac = [item.pins["cache.directory"] for item in plan.cells if item.cell.lane == "mac"]
    assert len(set(mac)) == len(mac) == 2


def test_a_mac_cell_banks_beside_its_own_logs(manifest: dict, tmp_path: Path) -> None:
    item = next(
        cell for cell in _plan(manifest, tmp_path, FULL_ENV).cells if cell.cell.id == "mac-local"
    )
    assert item.pins["cache.directory"] == str(tmp_path / "mac-local" / "cache")


def test_a_nas_cell_keeps_its_bank_and_shares_only_the_models(
    manifest: dict, tmp_path: Path
) -> None:
    """Detectors are downloaded once for the lane; nothing a reader decided is."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    run = str(next(step for step in item.steps if step.name == "run"))
    pull = str(next(step for step in item.steps if step.name == "pull-results"))

    assert "-v $MATRIX_NAS_CACHE:/models" in run
    assert "-v $MATRIX_NAS_OUT/nas-rules-local/cache:/cache" in run
    assert item.pins["cache.directory"] == "/cache"
    # Gigabytes of previews and thumbnails stay on the NAS, warm for a re-run.
    assert "--exclude=./cache" in pull


def test_a_cluster_cell_banks_on_its_own_subpath_of_the_shared_claim(
    manifest: dict, tmp_path: Path
) -> None:
    job = yaml.safe_load(_k8s_cell(manifest, tmp_path).manifests["job.yaml"])
    container = job["spec"]["template"]["spec"]["containers"][0]
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in job["spec"]["template"]["spec"]["volumes"]}

    assert mounts["/cache"]["subPath"] == "cache/k8s-rules-local"
    assert mounts["/models"]["subPath"] == "models"
    claim = "persistentVolumeClaim"
    assert volumes[mounts["/cache"]["name"]][claim]["claimName"] == "setup-matrix-data"
    assert volumes[mounts["/models"]["name"]][claim]["claimName"] == "setup-matrix-data"


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
    torn_down = [
        step.name for step in inference_overlay_steps(device="cpu", keep=False, lan=False, tag=TAG)
    ]
    kept = [
        step.name for step in inference_overlay_steps(device="cpu", keep=True, lan=False, tag=TAG)
    ]
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


def _nas_run(manifest: dict, tmp_path: Path, environment: dict) -> str:
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, environment).cells
        if cell.cell.id == "nas-rules-local"
    )
    return next(step for step in item.steps if step.name == "run").command[2]


def test_a_nas_cell_is_capped_at_four_cores_and_four_gigabytes_by_default(
    manifest: dict, tmp_path: Path
) -> None:
    assert "--cpus 4 --memory 4g" in _nas_run(manifest, tmp_path, FULL_ENV)


def test_a_nas_without_the_cfs_controller_can_ask_for_a_cpuset_instead(
    manifest: dict, tmp_path: Path
) -> None:
    """`--cpus` is a quota, and a kernel with no CFS bandwidth controller refuses it."""
    run = _nas_run(
        manifest,
        tmp_path,
        {**FULL_ENV, "MATRIX_NAS_DOCKER_LIMITS": "--cpuset-cpus 0-3 --memory 4g"},
    )
    assert "--cpuset-cpus 0-3 --memory 4g" in run
    assert "--cpus 4" not in run


def test_anything_but_a_resource_cap_is_refused_before_it_reaches_the_nas(
    manifest: dict, tmp_path: Path
) -> None:
    """The string is rendered verbatim into a command the NAS runs as root."""
    with pytest.raises(PlanError, match="--privileged is not a container resource flag"):
        _plan(
            manifest,
            tmp_path,
            {**FULL_ENV, "MATRIX_NAS_DOCKER_LIMITS": "--memory 4g --privileged true"},
        )


def test_the_nas_moves_its_files_with_tar_over_ssh(manifest: dict, tmp_path: Path) -> None:
    """The NAS ssh server has the SFTP subsystem off, and a modern scp speaks only SFTP."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    steps = {step.name: step for step in item.steps}

    assert steps["push-config"].command[0] == "tar"
    assert steps["push-config"].pipe_to[:2] == ("ssh", "$MATRIX_NAS_SSH")
    assert steps["pull-results"].command[:2] == ("ssh", "$MATRIX_NAS_SSH")
    assert steps["pull-results"].pipe_to[0] == "tar"
    assert not [step for step in item.steps if "scp" in str(step)]


_MKDIR = re.compile(r"mkdir -p ([^&|;]+)")


def _made_dirs(steps) -> set[str]:
    """Every directory these steps' remote shells create with `mkdir -p`."""
    return {
        directory
        for step in steps
        for text in (*step.command, *step.pipe_to)
        for match in _MKDIR.finditer(text)
        for directory in match.group(1).split()
    }


def _mount_sources(run: str) -> list[str]:
    """The host side of every `-v host:container` in a rendered `docker run`."""
    parts = shlex.split(run)
    pairs = zip(parts, parts[1:], strict=False)
    return [after.split(":", 1)[0] for flag, after in pairs if flag == "-v"]


def test_a_nas_cell_mounts_nothing_it_did_not_make(manifest: dict, tmp_path: Path) -> None:
    """Docker creates no bind source: a missing one is "Bind mount failed" and a dead cell."""
    nas = [item for item in _plan(manifest, tmp_path, FULL_ENV).cells if item.cell.lane == "nas"]
    assert nas

    for item in nas:
        names = [step.name for step in item.steps]
        run = next(step for step in item.steps if step.name == "run")
        owned = {
            source
            for source in _mount_sources(run.command[2])
            if source.startswith("$MATRIX_NAS_OUT")
        }
        assert owned, item.cell.id
        unmade = owned - _made_dirs(item.steps[: names.index("run")])
        assert not unmade, f"{item.cell.id} mounts {unmade}, which no earlier step creates"


def test_a_piped_step_prints_as_one_pipeline(manifest: dict, tmp_path: Path) -> None:
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    push = next(step for step in item.steps if step.name == "push-config")
    upstream, _, downstream = str(push).partition(" | ")
    assert upstream.startswith("tar -C ")
    assert downstream.startswith("ssh $MATRIX_NAS_SSH ")


def test_an_ssh_command_line_in_the_destination_variable_is_refused(
    manifest: dict, tmp_path: Path
) -> None:
    """It is substituted where ssh expects `[user@]host`; a command there dies far from here."""
    environment = {**FULL_ENV, "MATRIX_NAS_SSH": "ssh -i /nowhere/key someone@a-nas.invalid"}
    with pytest.raises(PlanError) as error:
        _plan(manifest, tmp_path, environment)
    assert "MATRIX_NAS_SSH" in str(error.value)
    assert "~/.ssh/config" in str(error.value)


def _k8s_cell(manifest: dict, tmp_path: Path):
    return next(
        item
        for item in _plan(manifest, tmp_path, FULL_ENV).cells
        if item.cell.id == "k8s-rules-local"
    )


def test_a_cluster_cell_mounts_claims_of_its_own(manifest: dict, tmp_path: Path) -> None:
    """The app's claims are RWO and attached to the running Deployment on one node."""
    item = _k8s_cell(manifest, tmp_path)
    claims = [doc for doc in yaml.safe_load_all(item.manifests["claims.yaml"]) if doc]

    assert [doc["metadata"]["name"] for doc in claims] == [
        "setup-matrix-data",
        "setup-matrix-output",
    ]
    assert all(doc["spec"]["accessModes"] == ["ReadWriteOnce"] for doc in claims)
    assert all("storageClassName" not in doc["spec"] for doc in claims), "use the default class"
    rendered = item.manifests["job.yaml"] + item.manifests["collector.yaml"]
    assert "immich-memories-models" not in rendered
    assert "immich-memories-output" not in rendered


def test_the_claims_are_applied_before_the_job_that_mounts_them(
    manifest: dict, tmp_path: Path
) -> None:
    names = [step.name for step in _k8s_cell(manifest, tmp_path).steps]
    assert names.index("apply-claims") < names.index("apply")
    # The data claim carries the models and the bank, so it outlives the cell.
    assert "delete-output-claim" in names
    assert not [name for name in names if name == "delete-data-claim"]


def test_a_leftover_job_is_dropped_before_the_cell_applies_its_own(
    manifest: dict, tmp_path: Path
) -> None:
    """A Job's `spec.template` is immutable, so `apply` over an interrupted run is rejected."""
    item = _k8s_cell(manifest, tmp_path)
    names = [step.name for step in item.steps]
    drop = str(next(step for step in item.steps if step.name == "drop-job"))

    assert names.index("drop-job") < names.index("apply-claims") < names.index("apply")
    assert "job/setup-matrix-k8s-rules-local" in drop
    assert "configmap/setup-matrix-k8s-rules-local-config" in drop
    assert "--ignore-not-found" in drop
    # Without the wait the next `apply` races a name the going pods still hold.
    assert "--wait=true" in drop
    # The drop is for what a crash left behind; the tear-down after the run stays.
    assert names.index("delete") > names.index("apply")


def test_a_leftover_collector_is_dropped_before_the_cell_applies_its_own(
    manifest: dict, tmp_path: Path
) -> None:
    item = _k8s_cell(manifest, tmp_path)
    names = [step.name for step in item.steps]
    drop = str(next(step for step in item.steps if step.name == "drop-collector"))

    assert names.index("drop-collector") < names.index("apply-collector") < names.index("copy-out")
    assert "pod/setup-matrix-k8s-rules-local-collect" in drop
    assert "--ignore-not-found" in drop
    assert "--wait=true" in drop
    assert names.index("delete-collector") > names.index("copy-out")


def test_no_pod_of_an_interrupted_run_is_left_holding_the_claim(
    manifest: dict, tmp_path: Path
) -> None:
    """The claims are ReadWriteOnce: a pod still terminating keeps the next cell Pending."""
    item = _k8s_cell(manifest, tmp_path)
    names = [step.name for step in item.steps]
    drain = str(next(step for step in item.steps if step.name == "drain-pods"))

    assert names.index("drop-job") < names.index("drain-pods") < names.index("apply-claims")
    assert "app.kubernetes.io/component=setup-matrix" in drain
    # `kubectl wait --for=delete` has no --ignore-not-found, and no pod at all is
    # the healthy case, so the drain is a bounded delete of the same selector.
    assert "--ignore-not-found" in drain
    assert "--timeout=3m" in drain


def test_the_pods_carry_the_label_the_drain_selects_on(manifest: dict, tmp_path: Path) -> None:
    """A label on the Job alone selects nothing: the drain matches pods, not Jobs."""
    job = yaml.safe_load(_k8s_cell(manifest, tmp_path).manifests["job.yaml"])
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/component"] == "setup-matrix"


def test_a_cell_that_gave_up_asks_for_events_as_well_as_the_pod(
    manifest: dict, tmp_path: Path
) -> None:
    """Events outlive the pod; a describe of a pod that is gone says `Events: <none>`."""
    names = [step.name for step in _k8s_cell(manifest, tmp_path).diagnostics]
    assert names == ["describe", "events"]


def test_a_cluster_cell_watches_for_a_failed_job_as_well_as_a_finished_one(
    manifest: dict, tmp_path: Path
) -> None:
    """A Job that failed never satisfies `--for=condition=complete`, so it is polled."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "k8s-rules-local"
    )
    wait = next(step for step in item.steps if step.name == "wait")
    assert "--for=condition=complete" not in str(wait)
    assert "succeeded=" in str(wait) and "failed=" in str(wait)


def test_a_cluster_cell_stops_waiting_on_a_pod_that_never_scheduled(
    manifest: dict, tmp_path: Path
) -> None:
    """A Pending pod never completes, and the three-hour wait was watching one."""
    item = _k8s_cell(manifest, tmp_path)
    steps = {step.name: step for step in item.steps}
    names = [step.name for step in item.steps]

    assert names.index("wait-scheduled") < names.index("wait")
    assert "--for=condition=PodScheduled" in steps["wait-scheduled"].command
    assert "--timeout=5m" in steps["wait-scheduled"].command
    assert item.diagnostics
    assert "describe" in item.diagnostics[0].command


def test_a_cluster_cell_asks_for_what_the_nas_cell_is_capped_at(
    manifest: dict, tmp_path: Path
) -> None:
    """Two rows in the same table, so neither may be given more room than the other."""
    job = yaml.safe_load(_k8s_cell(manifest, tmp_path).manifests["job.yaml"])
    resources = job["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["requests"] == {"memory": "4Gi", "cpu": "2000m"}
    assert resources["limits"] == {"memory": "4Gi", "cpu": "4000m"}


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


def test_the_runner_supplies_the_inference_address_so_no_cell_skips_for_it(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """End to end: nobody sets MATRIX_INFERENCE_BASE_URL and the NAS cells still run."""
    import setup_matrix

    # WHY: read_env_files merges os.environ under the env files, and the point of
    # the test is the one variable that is NOT in either.
    for name, value in FULL_ENV.items():
        if name != INFERENCE_ENV:
            monkeypatch.setenv(name, value)
    monkeypatch.delenv(INFERENCE_ENV, raising=False)

    exit_code = setup_matrix.main(
        [
            "--dry-run",
            "--serve-fixture",
            "--lane",
            "nas",
            "--env-file",
            str(tmp_path / "no-such.env"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    printed = capsys.readouterr()
    assert exit_code == 0
    assert INFERENCE_ENV not in printed.err, "no cell may be skipped for an address it derives"
    assert f"{INFERENCE_ENV}={DERIVED_ADDRESS}" in printed.out
    assert "nas-rules-service" in printed.out


def test_a_remote_cell_with_local_facts_fetches_the_pinned_models_first(
    manifest: dict, tmp_path: Path
) -> None:
    """Nothing else puts a model on a remote host, and a new models volume is empty."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    nas = next(item for item in plan.cells if item.cell.id == "nas-rules-local")
    script = nas.steps[[step.name for step in nas.steps].index("run")].command[2]

    fetch = script.index("models fetch")
    assert fetch < script.index("prepare --year"), "the fetch is what makes preparation possible"
    # Its own phase: a first fetch is hundreds of megabytes, and charging that to
    # preparation would publish a number about a network as a number about a NAS.
    assert "models_started=$SECONDS" in script
    assert "models-fetch-seconds.txt" in script


def test_a_remote_cell_taking_its_facts_off_the_service_fetches_nothing(
    manifest: dict, tmp_path: Path
) -> None:
    for cell_id in ("nas-rules-service", "k8s-hosted-zai"):
        item = next(i for i in _plan(manifest, tmp_path, FULL_ENV).cells if i.cell.id == cell_id)
        rendered = " ".join(str(step) for step in item.steps) + " ".join(item.manifests.values())
        assert "models fetch" not in rendered, cell_id


def test_every_model_root_of_a_local_facts_cell_is_on_the_shared_volume(
    manifest: dict, tmp_path: Path
) -> None:
    """Three roots is how the first run fetched into one place and read from another."""
    for cell_id in ("nas-rules-local", "k8s-rules-local"):
        pins = next(
            item for item in _plan(manifest, tmp_path, FULL_ENV).cells if item.cell.id == cell_id
        ).pins
        roots = [
            pins["triage.encoder"],
            pins["editorial.preparation.marqo_onnx"],
            pins["editorial.preparation.detector_cache_dir"],
        ]
        assert all(root.startswith("/models/") for root in roots), (cell_id, roots)
        assert pins["editorial.preparation.allow_model_downloads"] is True


def test_the_cluster_job_takes_its_model_roots_from_the_config_and_nowhere_else(
    manifest: dict, tmp_path: Path
) -> None:
    """Two sources for one path is how the Marqo export ended up off the volume."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "k8s-rules-local"
    )
    job = yaml.safe_load(item.manifests["job.yaml"])
    container = job["spec"]["template"]["spec"]["containers"][0]

    assert "env" not in container
    assert "/models/detectors/nsfw-marqo-384.onnx" in item.config_yaml
