"""The setup matrix renders the same plan every time, and never a secret.

The dry run IS the acceptance test for the runner: the ten cells are never run in
CI, so the only thing that can be asserted is that the plan they would run is the
right one, and that it can be read out loud without leaking a host or a key.
"""

from __future__ import annotations

import re
import shlex
import stat
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from matrix_pinned_config import pinned_config, read_operator_immich  # noqa: E402
from setup_matrix_plan import (  # noqa: E402
    DERIVED_ADDRESS,
    FROM_OPERATOR_CONFIG,
    GPU_PRODUCT_LABEL,
    IMMICH_KEY_ENV,
    INFERENCE_ENV,
    INFERENCE_IMAGE,
    LAN_OVERLAY,
    NODE_PRODUCT_PATH,
    REPO_ROOT,
    PlanError,
    build_plan,
    dry_run_text,
    inference_image,
    inference_node_command,
    inference_overlay_steps,
    load_manifest,
    needs_lan_address,
    node_product_command,
    overlay_skip_reason,
    pin_inference_node,
    read_cells,
    required_overlay_steps,
    required_overlays,
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
    "ZAI_API_KEY": "secret-zai-key",
    "ZAI_BASE_URL": "https://api.z.ai.invalid/api/anthropic",
    "MATRIX_FIXTURE_BASE_URL": "http://a-fixture.invalid:8078",
    # Supplied by the runner, never by the operator: it is read off the
    # LoadBalancer. Present here because build_plan is given what the runner
    # would have put in the environment by then.
    INFERENCE_ENV: DERIVED_ADDRESS,
}


TAG = "0.87.4"

# The operator's own config, which is what a cell's config is a copy of. Every
# path here exists on one Mac and nowhere else; the second real remote run pushed
# this `detector_python` to a NAS and the cell died in FileNotFoundError.
OPERATOR_CONFIG = {
    "immich": {"url": "http://immich.invalid:2283", "api_key": "operator-key"},
    "output": {"directory": "~/Videos/Memories", "resolution": "4K"},
    "cache": {
        "directory": "~/.immich-memories/cache",
        "database": "~/.immich-memories/cache.db",
    },
    "audio": {"local_music_dir": "~/Music/Memories"},
    "llm": {
        "provider": "openai-compatible",
        "base_url": "http://localhost:9999/v1",
        "model": "a-model-this-mac-has-resident",
        "thinking": False,
        "no_thinking_params": {"chat_template_kwargs": {"enable_thinking": False}},
    },
    "advanced": {
        "triage": {
            "encoder": "/Users/someone/.immich-memories/models/triage/dinov2-small.onnx",
            "bundle": "/Users/someone/heads/private-v4.npz",
        },
        "editorial": {
            "annotation_database": "/Users/someone/.immich-memories/annotations.sqlite",
            "preparation": {
                "head_bundle": "/Users/someone/heads/private-6heads.npz",
                "detector_python": "/Users/someone/.immich-memories-distill/venv/bin/python",
                "detector_cache_dir": "/Users/someone/.cache/huggingface",
                "marqo_onnx": "/Users/someone/models/nsfw-marqo-384.onnx",
            },
        },
    },
}

# A value that only resolves on the machine the config was written on: an
# absolute path into somebody's home, or a `~` that means a different directory
# in every container the matrix runs (the NAS sets HOME=/models, the cluster Job
# gets /home/immich, which goes away with the pod).
_ELSEWHERE = re.compile(rf"^\s*[\w.-]+: '?(~|/Users/|{re.escape(str(Path.home()))})")


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
        fresh_cache=overrides.get("fresh_cache", False),
    )


def test_the_manifest_holds_the_fourteen_setups(manifest: dict) -> None:
    cells = read_cells(manifest)
    assert len(cells) == 14
    assert {cell.lane for cell in cells} == {"mac", "nas", "k8s"}
    assert [cell.id for cell in cells][0] == "mac-local", "the reference cut must come first"


def test_a_full_environment_skips_nothing(manifest: dict, tmp_path: Path) -> None:
    """Every variable present and every overlay in the tree, so all fourteen run.

    The two full-tier cluster cells were the last holdout: they were declared
    against a tree that did not yet carry the captioner overlay they name.
    """
    plan = _plan(manifest, tmp_path, FULL_ENV)
    assert plan.skipped == ()
    assert len(plan.runnable) == 14


def test_the_nas_service_cells_need_no_hand_set_inference_address(
    manifest: dict, tmp_path: Path
) -> None:
    """The runner derives it from the LoadBalancer, so nobody has to look one up."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    for item in plan.cells:
        assert INFERENCE_ENV not in item.cell.requires_env, item.cell.id
    assert not [item for item in plan.skipped if INFERENCE_ENV in (item.skip_reason or "")]


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
    assert {name for name, reason in skipped.items() if "MATRIX_NAS_SSH" in reason} == {
        "nas-rules-local",
        "nas-rules-service",
        "nas-hosted-melious",
        "nas-hosted-zai",
    }


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


def test_a_nas_cell_hands_its_key_over_in_a_file_not_an_empty_flag(
    manifest: dict, tmp_path: Path
) -> None:
    """docker fills `-e NAME` from the shell running it, and ssh gives it none of ours.

    Both NAS hosted cells reached their provider with an empty key: Melious
    answered 401 while the same key worked from the cluster, where the runner
    makes a Secret out of its own environment.
    """
    plan = _plan(manifest, tmp_path, FULL_ENV)
    zai = next(item for item in plan.cells if item.cell.id == "nas-hosted-zai")
    rules = next(item for item in plan.cells if item.cell.id == "nas-rules-local")
    order = [step.name for step in zai.steps]
    steps = {step.name: str(step) for step in zai.steps}

    assert "-e ZAI_API_KEY" not in steps["run"]
    assert "--env-file $MATRIX_NAS_OUT/nas-hosted-zai/env" in steps["run"]
    assert "ZAI_API_KEY=$ZAI_API_KEY" in steps["push-env"]
    assert "umask 077" in steps["push-env"], "the file holds a key and nothing else may read it"
    assert order.index("push-env") < order.index("run") < order.index("drop-credentials")
    assert "--exclude=./env" in steps["pull-results"], "a key never comes back with the results"
    # A cell with no credential has no env file to write or to name.
    rules_steps = {step.name: str(step) for step in rules.steps}
    assert "push-env" not in rules_steps
    assert "--env-file" not in rules_steps["run"]


def test_the_config_pushed_to_the_nas_is_readable_by_nobody_else(
    manifest: dict, tmp_path: Path
) -> None:
    """It is the operator's own config with the pins over the top, key included.

    The NAS mounts its shares for the household, so the file is 0600 before it is
    tarred and the far side extracts under a umask that says the same thing. It
    is never pulled back, and it leaves with the env file whether or not the cell
    worked.
    """
    source = tmp_path / "operator.yaml"
    source.write_text(yaml.safe_dump(OPERATOR_CONFIG))
    plan = _plan(manifest, tmp_path, FULL_ENV, library="february")
    item = next(one for one in plan.cells if one.cell.id == "nas-rules-local")
    steps = {step.name: str(step) for step in item.steps}
    remote = "$MATRIX_NAS_OUT/nas-rules-local"

    written = pinned_config(source, tmp_path / "cell.yaml", item.pins)

    assert yaml.safe_load(written.read_text())["immich"]["api_key"] == "operator-key"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert "umask 077" in steps["push-config"]
    assert "--exclude=./config.yaml" in steps["pull-results"]
    assert f"rm -f {remote}/env {remote}/config.yaml" in steps["drop-credentials"]


def test_the_dry_run_prints_the_env_file_by_reference(manifest: dict, tmp_path: Path) -> None:
    """The transcript goes in a pull request, so it carries the name and never the key."""
    text = dry_run_text(_plan(manifest, tmp_path, FULL_ENV))

    assert "ZAI_API_KEY=$ZAI_API_KEY" in text
    assert all(FULL_ENV[name] not in text for name in ("ZAI_API_KEY", "MELIOUS_AI_KEY"))


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


def test_a_real_librarys_cluster_cell_is_told_where_immich_is(
    manifest: dict, tmp_path: Path
) -> None:
    """The ConfigMap is built from the pins, and a real library pins no Immich at all.

    Every k8s cell of run 2 died in the same second on "Immich not configured.
    Run 'immich-memories config' first." The URL is carried into the ConfigMap
    because a server address is not a secret; the key is not, because a ConfigMap
    is readable by anything that can read the namespace.
    """
    plan = _plan(manifest, tmp_path, FULL_ENV, library="february")
    for item in (one for one in plan.cells if one.cell.lane == "k8s"):
        config = yaml.safe_load(
            yaml.safe_load(item.manifests["configmap.yaml"])["data"]["config.yaml"]
        )
        create = str(next(step for step in item.steps if step.name == "make-secret"))
        assert config["immich"] == {"url": FROM_OPERATOR_CONFIG}, item.cell.id
        assert IMMICH_KEY_ENV not in item.manifests["configmap.yaml"], item.cell.id
        assert f"--from-literal={IMMICH_KEY_ENV}={FROM_OPERATOR_CONFIG}" in create
        assert "secretRef" in item.manifests["job.yaml"], item.cell.id


def test_the_fixture_library_still_brings_its_own_immich(manifest: dict, tmp_path: Path) -> None:
    """`demo` names the fixture server and a fake key, and nothing is carried for it."""
    plan = _plan(manifest, tmp_path, FULL_ENV)
    rules = next(item for item in plan.cells if item.cell.id == "k8s-rules-local")
    config = yaml.safe_load(
        yaml.safe_load(rules.manifests["configmap.yaml"])["data"]["config.yaml"]
    )

    assert config["immich"]["api_key"] == "fake-immich-api-key"
    assert config["immich"]["url"] == "$MATRIX_FIXTURE_BASE_URL"
    assert [step.name for step in rules.steps if "secret" in step.name] == []


def test_the_nas_reads_the_operators_key_out_of_its_own_config_file(
    manifest: dict, tmp_path: Path
) -> None:
    """Only the cluster needs the key carried: the NAS is handed the whole config.

    The file it gets is a copy of the operator's with the pins over the top, key
    included, which is why the NAS lane of run 2 ran when every cluster cell died.
    """
    source = tmp_path / "operator.yaml"
    source.write_text(yaml.safe_dump(OPERATOR_CONFIG))
    plan = _plan(manifest, tmp_path, FULL_ENV, library="february")
    nas = next(item for item in plan.cells if item.cell.id == "nas-rules-local")
    cluster = next(item for item in plan.cells if item.cell.id == "k8s-rules-local")

    pushed = yaml.safe_load(pinned_config(source, tmp_path / "nas.yaml", nas.pins).read_text())

    assert pushed["immich"]["api_key"] == OPERATOR_CONFIG["immich"]["api_key"]
    assert pushed["immich"]["url"] == OPERATOR_CONFIG["immich"]["url"]
    assert OPERATOR_CONFIG["immich"]["api_key"] not in str(cluster.manifests)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (None, "does not exist"),
        ({"immich": {"url": "http://immich.invalid:2283"}}, "names no immich.url"),
    ],
)
def test_an_operator_config_with_no_immich_stops_before_the_cluster_does(
    tmp_path: Path, config: dict | None, message: str
) -> None:
    """An empty Secret is the same dead Job, three hours later and harder to read."""
    source = tmp_path / "operator.yaml"
    if config is not None:
        source.write_text(yaml.safe_dump(config))

    with pytest.raises(SystemExit, match=message):
        read_operator_immich(source)


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


@pytest.mark.parametrize("lane", ["nas", "k8s"])
def test_a_remote_cell_carries_no_path_off_the_operators_machine(
    manifest: dict, tmp_path: Path, lane: str
) -> None:
    """A cell's config is a copy of the operator's, and a container is not that machine.

    `nas-rules-local` reached `detectors: FileNotFoundError` on a venv interpreter
    under /Users and published no cut at all, because the pins only overwrite the
    fields they name and every other path came along for the ride.
    """
    source = tmp_path / "operator.yaml"
    source.write_text(yaml.safe_dump(OPERATOR_CONFIG))
    for item in _plan(manifest, tmp_path, FULL_ENV, lanes=(lane,)).cells:
        rendered = pinned_config(source, tmp_path / f"{item.cell.id}.yaml", item.pins).read_text()
        elsewhere = [line for line in rendered.splitlines() if _ELSEWHERE.match(line)]
        assert elsewhere == [], f"{item.cell.id} carries {elsewhere}"
        config = yaml.safe_load(rendered)
        preparation = config["advanced"]["editorial"]["preparation"]
        assert preparation["detector_python"] == "", "blank is the interpreter running the cell"
        assert preparation["marqo_onnx"].startswith("/models/")
        assert config["output"]["directory"] == "/out"
        assert config["cache"]["directory"] == "/cache"


def test_a_hosted_cell_does_not_inherit_the_operators_llm_dialect(
    manifest: dict, tmp_path: Path
) -> None:
    """That block describes the server on the operator's desk, not somebody's API.

    `no_thinking_params` is oMLX's own chat-template switch, and z.ai answers a
    request carrying it with a 200 whose body is a 404. The provider preset would
    supply the right dialect and the right URL, but it fills a field only where it
    is still at the model's default, so the copied value has to go rather than be
    written over.
    """
    source = tmp_path / "operator.yaml"
    source.write_text(yaml.safe_dump(OPERATOR_CONFIG))
    plan = _plan(manifest, tmp_path, FULL_ENV)

    def rendered(cell_id: str) -> dict:
        item = next(one for one in plan.cells if one.cell.id == cell_id)
        return yaml.safe_load(
            pinned_config(source, tmp_path / f"{cell_id}.yaml", item.pins).read_text()
        )

    zai = rendered("k8s-hosted-zai")
    assert zai["llm"]["provider"] == "zai"
    assert "no_thinking_params" not in zai["llm"]
    # The drop is a default, and this cell names its own endpoint: the coding
    # plan is served by the Anthropic-compatible route and nothing else.
    assert zai["llm"]["base_url"] == "$ZAI_BASE_URL"

    # A hosted cell that names its own endpoint still gets the one it named.
    melious = rendered("nas-hosted-melious")
    assert melious["llm"]["base_url"] == "$MELIOUS_AI_BASE_URL"
    assert "no_thinking_params" not in melious["llm"]

    # The reference cell reads the operator's own server, and keeps its dialect.
    assert rendered("mac-local")["llm"]["no_thinking_params"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_the_collector_copies_from_the_directory_the_job_wrote_to(
    manifest: dict, tmp_path: Path
) -> None:
    """The archive is rooted where the Job wrote, not where the image's WORKDIR is.

    A source relative to the claim root was `tar: setup-matrix/<cell>: Cannot
    stat` on the second real run: the film, the attempt and every per-step log
    stayed on the volume and the cell published an empty row.
    """
    item = _k8s_cell(manifest, tmp_path)
    container = yaml.safe_load(item.manifests["job.yaml"])["spec"]["template"]["spec"][
        "containers"
    ][0]
    collector = yaml.safe_load(item.manifests["collector.yaml"])["spec"]["containers"][0]
    written = _output_mount(container)
    read_back = _output_mount(collector)
    copy_out = next(step for step in item.steps if step.name == "copy-out")
    source = copy_out.command[copy_out.command.index("-C") + 1]

    assert source.startswith("/"), "a relative source resolves against the image's WORKDIR"
    assert source == written["mountPath"] == read_back["mountPath"]
    assert written["subPath"] == read_back["subPath"] == f"setup-matrix/{item.cell.id}"
    assert copy_out.command[copy_out.command.index("exec") + 1].endswith("-collect")
    assert f"tee {source}/generate.log" in container["command"][-1]


def test_a_cluster_cell_pulls_its_results_through_a_pipe_not_kubectl_cp(
    manifest: dict, tmp_path: Path
) -> None:
    """`kubectl cp` ends its stream early, and `k8s-rules-service` paid for it twice in one run.

    Both copies reported `error: unexpected EOF` with the three retries spent, and
    the cell published no film, no attempt and no per-phase log. This is the same
    tar-over-a-pipe the NAS lane pulls with: the remote tar writes, the local one
    reads, and nothing in between decides the bytes have stopped.
    """
    item = _k8s_cell(manifest, tmp_path)
    copy_out = next(step for step in item.steps if step.name == "copy-out")

    assert "cp" not in copy_out.command
    assert copy_out.command[:4] == ("kubectl", "--context", "$MATRIX_K8S_CONTEXT", "-n")
    assert copy_out.command[-7:] == ("--", "tar", "-C", "/out", "-cf", "-", ".")
    assert copy_out.pipe_to[0] == "tar"
    assert copy_out.pipe_to[-2:] == ("-xf", "-")
    assert str(copy_out).count(" | ") == 1


def test_a_remote_cell_leaves_what_it_pulls_back_readable(manifest: dict, tmp_path: Path) -> None:
    """The NAS container runs as root, and the ssh user who tars the results back does not.

    `cp -a` carried the app's own 0700 onto the copied attempt directory and
    `pull-results` exited 2 on `tar: ./attempts/nas-rules-local: Cannot open:
    Permission denied`, so the cell published an empty cut beside a film that had
    come back intact.
    """
    script = _container_command(_k8s_cell(manifest, tmp_path))
    chmod = next(line for line in script.splitlines() if line.startswith("chmod"))

    assert "-R a+rX" in chmod
    for opened in ("/out/attempts", "/out/*.txt", "/out/*.log", "/out/k8s-rules-local_*"):
        assert opened in chmod
    # The NAS binds the same directory that holds the credentials file and the
    # cell's own cache, and neither is anyone else's to read.
    assert "/out/env" not in chmod
    assert "/cache" not in chmod
    assert script.index("cp -a /cache/editorial-runs") < script.index("chmod")
    assert script.index("/out/cpu.txt") < script.index("chmod")


def test_a_remote_cell_copies_its_own_attempt_out_of_a_cache_nothing_pulls_back(
    manifest: dict, tmp_path: Path
) -> None:
    """The cut, the trace and the projection are inside the cache, and the cache stays put.

    Both cluster cells that finished published `selected_asset_ids: []` and
    `#kept 0` beside a film that plainly had pictures in it, because the attempt
    was on the per-cell editorial cache and no copy step ever looked there.
    """
    script = _container_command(_k8s_cell(manifest, tmp_path))

    assert "cp -a /cache/editorial-runs/k8s-rules-local /out/attempts/" in script
    # After the exit code has been taken: $PIPESTATUS describes the last pipeline
    # that ran, so a copy in between would be reporting its own status as the run's.
    assert script.index("rc=${PIPESTATUS[0]}") < script.index("cp -a /cache/editorial-runs")


def test_a_remote_cell_records_whether_its_cache_already_held_a_run(
    manifest: dict, tmp_path: Path
) -> None:
    """The kubelet creates the cache subPath before the container starts, so it proves nothing."""
    script = _container_command(_k8s_cell(manifest, tmp_path))

    assert "[ -e /cache/.setup-matrix-cell ] && echo primed > /out/cache-primed.txt" in script
    assert "touch /cache/.setup-matrix-cell" in script
    assert script.index("cache-primed.txt") < script.index("prepare")


def _lane_cells(manifest: dict, tmp_path: Path, **overrides):
    """One cell of each lane, so a switch can be asserted on all three at once."""
    plan = _plan(manifest, tmp_path, FULL_ENV, **overrides)
    wanted = {"mac-rules", "nas-rules-local", "k8s-rules-local"}
    return {item.cell.id: item for item in plan.cells if item.cell.id in wanted}


def _removals(item) -> list[str]:
    """Every `rm -rf` this cell runs, wherever it runs it, one command per entry."""
    text = " ".join(str(step) for step in item.steps)
    text += " " + (_container_command(item) if item.manifests else "")
    return [match.group(0).strip() for match in re.finditer(r"rm -rf [^;\n|&]*", text)]


def test_a_cell_keeps_its_bank_unless_the_run_asks_for_a_fresh_one(
    manifest: dict, tmp_path: Path
) -> None:
    """Warm is the default: a bank kept between runs is what makes a re-run affordable."""
    for item in _lane_cells(manifest, tmp_path).values():
        assert item.fresh_cache is False
        assert _removals(item) == [], item.cell.id
    cluster = _lane_cells(manifest, tmp_path)["k8s-rules-local"]
    assert "MATRIX_FRESH_CACHE" not in cluster.manifests["job.yaml"]


def test_a_fresh_cache_run_empties_every_lanes_bank_and_no_models(
    manifest: dict, tmp_path: Path
) -> None:
    """Both hosted Melious cells made 0 completions and replayed a bank two runs old.

    `selection 2s` and `selection 7s` were warm replays published as this run's
    numbers, and `prep cold 0s` beside them. Each lane empties its own cache a
    different way, because on each one a different thing owns that directory.
    """
    cells = _lane_cells(manifest, tmp_path, fresh_cache=True)
    assert all(item.fresh_cache for item in cells.values())

    mac = next(step for step in cells["mac-rules"].steps if step.name == "fresh-cache")
    assert str(mac).startswith("rm -rf ") and str(mac).endswith("/mac-rules/cache")
    assert [step.name for step in cells["mac-rules"].steps][0] == "fresh-cache"

    nas = str(
        next(step for step in cells["nas-rules-local"].steps if step.name == "make-remote-dir")
    )
    assert "rm -rf $MATRIX_NAS_OUT/nas-rules-local/cache/*" in nas
    assert nas.index("rm -rf") < nas.index("mkdir -p")

    job = yaml.safe_load(cells["k8s-rules-local"].manifests["job.yaml"])
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [{"name": "MATRIX_FRESH_CACHE", "value": "1"}]
    script = _container_command(cells["k8s-rules-local"])
    assert '[ "${MATRIX_FRESH_CACHE:-0}" = 1 ] && rm -rf /cache/*' in script

    # The model files are the whole matrix's, shared across every cell, and
    # re-fetching them would measure a network rather than a setup.
    for item in cells.values():
        removals = _removals(item)
        assert removals, item.cell.id
        assert all("/models" not in removal for removal in removals), removals
        assert all("cache" in removal for removal in removals), removals


def test_a_fresh_cache_cell_reports_itself_cold_rather_than_looking(
    manifest: dict, tmp_path: Path
) -> None:
    """The wipe and the answer are the same step, so there is nothing left to look at.

    Both remote lanes report `prepare_cache_primed` out of what they printed: the
    NAS from this ssh, the cluster from a marker file the container leaves. The
    marker is a dotfile, which `rm -rf <cache>/*` on its own would walk straight
    past.
    """
    cells = _lane_cells(manifest, tmp_path, fresh_cache=True)
    nas = str(
        next(step for step in cells["nas-rules-local"].steps if step.name == "make-remote-dir")
    )
    script = _container_command(cells["k8s-rules-local"])

    assert "echo cold" in nas and "echo primed" not in nas
    assert "/cache/.[!.]*" in script
    assert script.index("rm -rf /cache") < script.index("/cache/.setup-matrix-cell")


def test_a_nas_cell_looks_at_its_cache_before_it_creates_it(manifest: dict, tmp_path: Path) -> None:
    """`mkdir -p` runs one line later, and would make the answer yes on every run."""
    item = next(
        cell
        for cell in _plan(manifest, tmp_path, FULL_ENV).cells
        if cell.cell.id == "nas-rules-local"
    )
    command = str(next(step for step in item.steps if step.name == "make-remote-dir"))

    assert "test -d $MATRIX_NAS_OUT/nas-rules-local/cache && echo primed || echo cold" in command
    assert command.index("test -d") < command.index("mkdir -p")


def _container_command(item) -> str:
    job = yaml.safe_load(item.manifests["job.yaml"])
    return job["spec"]["template"]["spec"]["containers"][0]["command"][-1]


def _output_mount(container: dict) -> dict:
    return next(mount for mount in container["volumeMounts"] if mount["name"] == "output")


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

    # The pod is asked for before it is waited on: `kubectl wait` treats a
    # selector that matches nothing as an error, and `apply` returns before the
    # Job controller has created anything.
    assert names.index("apply") < names.index("wait-created") < names.index("wait-scheduled")
    assert names.index("wait-scheduled") < names.index("wait")
    assert steps["wait-created"].command[-4:] == (
        "-l",
        "job-name=setup-matrix-k8s-rules-local",
        "-o",
        "name",
    )
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


# --- the two GPU render cells ------------------------------------------------
#
# They exist to answer two questions the ten CPU cells cannot: "when do you need
# a GPU", and "T1000 or 1070". Both answers are only worth having if the render
# really happened on the named card, so what is asserted here is the pin.


@pytest.mark.parametrize(
    ("cell_id", "product"),
    [
        ("k8s-gpu-t1000", "NVIDIA-T1000-8GB-SHARED"),
        ("k8s-gpu-1070", "NVIDIA-GeForce-GTX-1070-SHARED"),
    ],
)
def test_a_gpu_cell_selects_one_card_by_label(
    manifest: dict, tmp_path: Path, cell_id: str, product: str
) -> None:
    """A node label, never a hostname: a host moves, and a transcript is published."""
    item = _cell(manifest, tmp_path, cell_id)
    pod = yaml.safe_load(item.manifests["job.yaml"])["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert pod["runtimeClassName"] == "nvidia"
    assert pod["nodeSelector"] == {GPU_PRODUCT_LABEL: product}
    assert {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"} in pod[
        "tolerations"
    ]
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert _job_env(container) == {
        "NVIDIA_VISIBLE_DEVICES": "all",
        "NVIDIA_DRIVER_CAPABILITIES": "compute,video,utility",
    }


def test_a_gpu_cell_asks_for_the_same_cpu_as_every_other_cell(
    manifest: dict, tmp_path: Path
) -> None:
    """Only the render device changes, or the row is not comparable with the rest."""
    gpu = yaml.safe_load(_cell(manifest, tmp_path, "k8s-gpu-t1000").manifests["job.yaml"])
    cpu = yaml.safe_load(_cell(manifest, tmp_path, "k8s-rules-service").manifests["job.yaml"])

    def resources(job: dict) -> dict:
        return job["spec"]["template"]["spec"]["containers"][0]["resources"]

    assert resources(gpu)["requests"] == resources(cpu)["requests"]
    assert resources(gpu)["limits"]["cpu"] == resources(cpu)["limits"]["cpu"]


def test_a_gpu_cell_names_its_encoder_instead_of_detecting_one(
    manifest: dict, tmp_path: Path
) -> None:
    """Detection takes the first backend that can encode, and software counts as none.

    A pod whose driver capabilities came up short would encode on the CPU and
    publish it as a GPU row. Named, the miss is a warning in the log.
    """
    pins = _cell(manifest, tmp_path, "k8s-gpu-t1000").pins
    assert pins["hardware.backend"] == "nvidia"
    assert "hardware.backend" not in _cell(manifest, tmp_path, "k8s-rules-service").pins


def test_the_driver_capabilities_are_what_puts_nvenc_in_the_container(
    manifest: dict, tmp_path: Path
) -> None:
    """The first cluster Jobs got `compute,utility` and every NVENC probe died on -22.

    They had drawn their titles on CUDA off the shared card by then, so the run
    looked accelerated and the film was still encoded in software.
    """
    for cell_id in ("k8s-gpu-t1000", "k8s-gpu-1070"):
        job = yaml.safe_load(_cell(manifest, tmp_path, cell_id).manifests["job.yaml"])
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert _job_env(container)["NVIDIA_DRIVER_CAPABILITIES"] == "compute,video,utility"


def test_no_cpu_cell_asks_the_cluster_for_a_card(manifest: dict, tmp_path: Path) -> None:
    """No runtime class, no device limit, and none of the driver variables either."""
    for item in _plan(manifest, tmp_path, FULL_ENV).cells:
        if item.cell.gpu_product or "job.yaml" not in item.manifests:
            continue
        job = item.manifests["job.yaml"]
        assert "nvidia" not in job, item.cell.id
        assert "NVIDIA_" not in job, item.cell.id


def test_the_node_label_is_read_off_the_pod_rather_than_the_deployment() -> None:
    """The Deployment says where the pod was asked to go. The pod says where it went."""
    assert "spec.nodeName" in inference_node_command()[-1]
    assert NODE_PRODUCT_PATH in " ".join(node_product_command("a-node"))
    # A jsonpath splits on dots, so a label name has to escape its own.
    assert "nvidia\\.com/gpu\\.product" in NODE_PRODUCT_PATH


# --- the inference service on a named card ------------------------------------


def test_the_inference_service_can_be_pinned_to_one_card() -> None:
    """Rendered at apply time, the way the image tag is. Nothing is committed."""
    rendered = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: immich-memories-inference\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: inference\n"
    )
    pinned = yaml.safe_load(pin_inference_node(rendered, "NVIDIA-T1000-8GB-SHARED"))
    assert pinned["spec"]["template"]["spec"]["nodeSelector"] == {
        GPU_PRODUCT_LABEL: "NVIDIA-T1000-8GB-SHARED"
    }
    assert pin_inference_node(rendered, "") == rendered, "unpinned is the default"


def test_a_pinned_service_says_so_in_the_dry_run() -> None:
    steps = inference_overlay_steps(
        device="cuda", keep=False, lan=False, tag=TAG, node_product="NVIDIA-GeForce-GTX-1070-SHARED"
    )
    assert f"{GPU_PRODUCT_LABEL}=NVIDIA-GeForce-GTX-1070-SHARED" in " ".join(
        str(step) for step in steps
    )
    unpinned = inference_overlay_steps(device="cuda", keep=False, lan=False, tag=TAG)
    assert "inference-node" not in [step.name for step in unpinned]


# --- the full tier in the cluster ---------------------------------------------

CAPTIONER_OVERLAY = "deploy/kubernetes/overlays/captioner"


def test_the_full_tier_cluster_cells_point_at_the_captioner_service(manifest: dict) -> None:
    """The URL is the Service's own name and port, read off the overlay that serves it.

    Spelled by hand it would be right until somebody renamed the Service, and a
    `tier: full` cell pointing at a name nothing answers fails on its first
    picture, hours into a run.
    """
    documents = yaml.safe_load_all((REPO_ROOT / CAPTIONER_OVERLAY / "service.yaml").read_text())
    service = next(d for d in documents if d and d["kind"] == "Service")
    url = f"http://{service['metadata']['name']}:{service['spec']['ports'][0]['port']}/v1"

    for cell_id in ("k8s-full-rules", "k8s-full-melious"):
        cell = next(cell for cell in read_cells(manifest) if cell.id == cell_id)
        assert cell.tier == "full"
        assert cell.config["editorial.preparation.caption_base_url"] == url


def test_the_full_tier_cluster_cells_run_now_the_overlay_is_here(
    manifest: dict, tmp_path: Path
) -> None:
    """Declared before the overlay existed, skipped with a reason, and now runnable."""
    plan = _plan(manifest, tmp_path, FULL_ENV)

    assert {"k8s-full-rules", "k8s-full-melious"} <= {item.cell.id for item in plan.runnable}
    assert required_overlays(read_cells(manifest)) == (CAPTIONER_OVERLAY,)


def test_a_cell_whose_overlay_is_absent_is_still_skipped_with_a_reason(
    manifest: dict, tmp_path: Path
) -> None:
    """The gate stays behind the cells that passed it: a checkout can carry less."""
    cell = next(cell for cell in read_cells(manifest) if cell.id == "k8s-full-rules")
    assert overlay_skip_reason(cell, tmp_path) == "captioner overlay not in this tree yet"
    assert required_overlays((cell,), tmp_path) == ()

    (tmp_path / cell.requires_overlay).mkdir(parents=True)
    assert overlay_skip_reason(cell, tmp_path) is None
    assert required_overlays((cell,), tmp_path) == (cell.requires_overlay,)


def test_a_declared_overlay_comes_down_with_the_inference_one() -> None:
    """`--keep-service` keeps both: a run that kept one service meant to keep the other."""
    path = "deploy/kubernetes/overlays/captioner"
    torn_down = [step.name for step in required_overlay_steps(path, keep=False)]
    kept = [step.name for step in required_overlay_steps(path, keep=True)]

    assert torn_down == ["apply-captioner", "delete-captioner"]
    assert kept == ["apply-captioner"]
    assert path in str(required_overlay_steps(path, keep=False)[0])


def _job_env(container: dict) -> dict[str, str]:
    return {entry["name"]: entry["value"] for entry in container.get("env") or []}


def _cell(manifest: dict, tmp_path: Path, cell_id: str):
    return next(
        item for item in _plan(manifest, tmp_path, FULL_ENV).cells if item.cell.id == cell_id
    )
