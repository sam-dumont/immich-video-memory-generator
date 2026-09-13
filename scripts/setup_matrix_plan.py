"""The setup matrix as a plan: cells, their config pins, and every command a lane runs.

Nothing here touches the network, the filesystem or the clock. `--dry-run` prints
exactly what this module returns, which is why the dry run is the acceptance test
for the whole runner: if the plan is right, the only thing left is to execute it.

Environment references are resolved here, but their VALUES never reach a rendered
command. A plan carries `$MATRIX_NAS_SSH`, not the ssh destination, so a transcript
can go in a pull request and a unit test can run with no env file at all. The
executor substitutes values at the last moment, from `Plan.environment`.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MANIFEST = Path(__file__).resolve().parent / "setup_matrix.yaml"
SCHEMA = "setup-matrix-v1"

# `$env:NAME` is resolved by the runner. `${NAME}` is left for the app to expand.
_ENV_REFERENCE = re.compile(r"^\$env:([A-Z][A-Z0-9_]*)$")
_APP_REFERENCE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")

# The fixture library binds here so the NAS and the cluster can reach the Mac.
FIXTURE_PORT = 8078
FIXTURE_ENV = "MATRIX_FIXTURE_BASE_URL"

# One container filesystem layout, shared by both remote lanes so the capture
# code has a single set of paths to read back.
REMOTE_CONFIG = "/out/config.yaml"
REMOTE_OUT = "/out"
REMOTE_CACHE = "/models/.immich-memories/cache"
# Where a cluster cell writes inside the shared output claim, so the collector
# pod and the Job agree on one path.
OUTPUT_SUBPATH = "setup-matrix"

PREPARE_COLD_LOG = "prepare-cold.log"
PREPARE_WARM_LOG = "prepare-warm.log"
GENERATE_LOG = "generate.log"
PEAK_RSS_FILE = "peak-rss-bytes.txt"
CPU_FILE = "cpu.txt"


class PlanError(RuntimeError):
    """The manifest or the request cannot produce a runnable plan."""


@dataclass(frozen=True)
class Cell:
    """One setup: where it runs, who reads, where the picture facts come from."""

    id: str
    lane: str
    reader: str
    facts: str
    tier: str
    why: str
    requires_env: tuple[str, ...]
    config: dict[str, Any]
    inference_overlay: bool

    @property
    def hosted(self) -> bool:
        return self.reader.startswith("hosted_")


@dataclass(frozen=True)
class Step:
    """One command in a cell's sequence, rendered with variable names not values."""

    name: str
    command: tuple[str, ...]

    def __str__(self) -> str:
        """The command as a shell would have to be given it.

        `shlex.quote` rather than wrapping in quotes: the NAS step already carries
        a quoted script inside its argument, and single quotes do not nest. Naive
        wrapping printed a line that looked runnable and was not.
        """
        return " ".join(part if _plain(part) else shlex.quote(part) for part in self.command)


@dataclass(frozen=True)
class CellPlan:
    """What one cell will do, as text a person can read and a shell could run."""

    cell: Cell
    pins: dict[str, Any]
    config_yaml: str
    steps: tuple[Step, ...]
    manifests: dict[str, str]
    app_credentials: tuple[str, ...]
    cache_dir: str
    skip_reason: str | None = None


@dataclass(frozen=True)
class Plan:
    """Every cell the request asked for, with what each needs and what each will run."""

    library: str
    month: str
    image: str
    anonymize_required: bool
    cells: tuple[CellPlan, ...]
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def runnable(self) -> tuple[CellPlan, ...]:
        return tuple(item for item in self.cells if item.skip_reason is None)

    @property
    def skipped(self) -> tuple[CellPlan, ...]:
        return tuple(item for item in self.cells if item.skip_reason is not None)


def load_manifest(path: Path | None = None) -> dict:
    data = yaml.safe_load((path or MANIFEST).read_text()) or {}
    if data.get("schema") != SCHEMA:
        raise PlanError(f"{path or MANIFEST} is not a {SCHEMA} manifest")
    return data


def read_cells(manifest: dict) -> tuple[Cell, ...]:
    return tuple(
        Cell(
            id=str(row["id"]),
            lane=str(row["lane"]),
            reader=str(row["reader"]),
            facts=str(row["facts"]),
            tier=str(row["tier"]),
            why=str(row.get("why", "")),
            requires_env=tuple(row.get("requires_env") or ()),
            config=dict(row.get("config") or {}),
            inference_overlay=bool(row.get("inference_overlay", False)),
        )
        for row in manifest["cells"]
    )


def env_reference(value: Any) -> str | None:
    """The variable a `$env:NAME` pin names, or None for a literal."""
    match = _ENV_REFERENCE.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def app_credential(value: Any) -> str | None:
    """The variable a `${NAME}` pin leaves for the app to expand at load time."""
    match = _APP_REFERENCE.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def _render_pin(value: Any) -> Any:
    """A pin as it is written to the config file: `$env:NAME` becomes `$NAME` text."""
    name = env_reference(value)
    return f"${name}" if name else value


def month_parts(month: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise PlanError(f"a month wants YYYY-MM, got {month!r}")
    year, _, number = month.partition("-")
    return year, str(int(number))


def _variables(source: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The `$env:` names a cell resolves, and the `${}` names the app expands."""
    references: list[str] = []
    credentials: list[str] = []
    for value in source.values():
        if name := env_reference(value):
            references.append(name)
        elif name := app_credential(value):
            credentials.append(name)
    return tuple(dict.fromkeys(references)), tuple(dict.fromkeys(credentials))


def _pins_for(cell: Cell, manifest: dict, library: dict) -> dict[str, Any]:
    pins: dict[str, Any] = dict(manifest.get("baseline_config") or {})
    pins.update(library.get("config") or {})
    pins["editorial.preparation.tier"] = cell.tier
    pins.update(cell.config)
    return {key: _render_pin(value) for key, value in pins.items()}


def _scope(month: str) -> list[str]:
    year, number = month_parts(month)
    return ["--year", year, "--month", number]


def _mac_steps(cell: Cell, memory: dict, month: str, out_dir: Path) -> tuple[Step, ...]:
    """Three local invocations. Cold, warm, then the cut, each timed by the runner."""
    cell_dir = out_dir / cell.id
    root = ["uv", "run", "immich-memories", "--config", str(cell_dir / "config.yaml")]
    generate = [
        *root,
        "generate",
        "--memory-type",
        str(memory["memory_type"]),
        *_scope(month),
        "--duration",
        str(memory["duration"]),
        # WHY: the attempt directory is named after the memory key, so pinning it
        # to the cell id is the only way to know which directory to read back.
        "--memory-key",
        cell.id,
        "--output",
        str(cell_dir / f"{cell.id}.mp4"),
        "--quiet",
    ]
    prepare = [*root, "prepare", *_scope(month)]
    return (
        Step("prepare-cold", tuple(prepare)),
        Step("prepare-warm", tuple(prepare)),
        Step("generate", tuple(generate)),
    )


def _container_script(cell: Cell, memory: dict, month: str) -> str:
    """What a remote container runs: prepare twice, cut, then report its own cost.

    The published image is `python:3.11-slim` underneath, which carries no
    `/usr/bin/time`. The kernel already counts what we want, so the script reads
    the cgroup files on the way out and writes them beside the video. Both cgroup
    generations are tried because DSM and the cluster do not agree on which one
    they are on; when neither answers, the capture records the field as unmeasured
    rather than guessing.
    """
    scope = " ".join(_scope(month))
    root = f"immich-memories --config {REMOTE_CONFIG}"
    cut = (
        f"{root} generate --memory-type {memory['memory_type']} {scope} "
        f"--duration {memory['duration']} --memory-key {cell.id} "
        f"--output {REMOTE_OUT}/{cell.id}.mp4 --quiet"
    )
    return "; ".join(
        [
            "set -u",
            f"{root} prepare {scope} 2>&1 | tee {REMOTE_OUT}/{PREPARE_COLD_LOG}",
            f"{root} prepare {scope} 2>&1 | tee {REMOTE_OUT}/{PREPARE_WARM_LOG}",
            f"{cut} 2>&1 | tee {REMOTE_OUT}/{GENERATE_LOG}",
            "rc=${PIPESTATUS[0]}",
            f"cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes "
            f"2>/dev/null | head -1 > {REMOTE_OUT}/{PEAK_RSS_FILE}",
            f"cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/cpuacct/cpuacct.usage "
            f"2>/dev/null > {REMOTE_OUT}/{CPU_FILE}",
            "exit $rc",
        ]
    )


def _credential_flags(cell: Cell) -> list[str]:
    """`-e NAME` per credential: docker takes the value from the ssh session's env.

    The value never enters the command, so a rendered plan carries no key even
    when it is executed for real.
    """
    _, credentials = _variables(cell.config)
    return [flag for name in credentials for flag in ("-e", name)]


def _nas_steps(cell: Cell, memory: dict, month: str, out_dir: Path, image: str) -> tuple[Step, ...]:
    """ssh, one docker run, scp back.

    WHY the single quoted string: `ssh host a b c` concatenates its arguments and
    hands the result to the remote shell, which re-splits them. Passing the docker
    command as separate argv entries would lose the grouping of `-lc <script>` and
    the remote bash would try to run the script's first word as a command.
    """
    remote = f"$MATRIX_NAS_OUT/{cell.id}"
    local = out_dir / cell.id
    docker = [
        "$MATRIX_NAS_DOCKER",
        "run",
        "--rm",
        "--cpus",
        "4",
        "--memory",
        "4g",
        "--device",
        "/dev/dri",
        # WHY root: the NAS mounts its shares with an ownership the image's uid
        # 1000 cannot write, and the run has to write its cache and its video.
        "--user",
        "0:0",
        "-v",
        "$MATRIX_NAS_CACHE:/models",
        "-e",
        "HOME=/models",
        "-v",
        f"{remote}:{REMOTE_OUT}",
        *_credential_flags(cell),
        image,
        "/bin/bash",
        "-lc",
        shlex.quote(_container_script(cell, memory, month)),
    ]
    return (
        Step("make-remote-dir", ("ssh", "$MATRIX_NAS_SSH", f"mkdir -p {remote}")),
        Step("push-config", ("scp", str(local / "config.yaml"), f"$MATRIX_NAS_SSH:{remote}/")),
        Step("run", ("ssh", "$MATRIX_NAS_SSH", " ".join(docker))),
        Step("pull-results", ("scp", "-r", f"$MATRIX_NAS_SSH:{remote}/.", str(local))),
    )


def _secret_steps(cell: Cell, context: tuple[str, ...]) -> tuple[Step, ...]:
    """Put the cell's credentials in a Secret without ever writing one to a file.

    The value only exists in the argv of the create call, substituted the moment
    it runs. Delete-then-create rather than `apply` because `create secret` has no
    idempotent form that does not need a shell pipe.
    """
    _, credentials = _variables(cell.config)
    if not credentials:
        return ()
    name = f"setup-matrix-{cell.id}-secrets"
    literals = [f"--from-literal={variable}=${variable}" for variable in credentials]
    return (
        Step("drop-secret", (*context, "delete", "secret", name, "--ignore-not-found")),
        Step("make-secret", (*context, "create", "secret", "generic", name, *literals)),
    )


def _k8s_steps(cell: Cell, out_dir: Path) -> tuple[Step, ...]:
    """Apply, wait, then copy the results out through a collector pod.

    WHY a collector: `kubectl cp` shells into the pod to run tar, and a finished
    Job's pod has no running container to shell into. The collector mounts the
    same output claim, stays up while the copy happens, and is deleted after.
    """
    context = KUBECTL
    name = f"setup-matrix-{cell.id}"
    collector = f"{name}-collect"
    local = out_dir / cell.id
    return (
        *_secret_steps(cell, context),
        Step(
            "apply",
            (*context, "apply", "-f", str(local / "configmap.yaml"), "-f", str(local / "job.yaml")),
        ),
        Step("wait", (*context, "wait", f"job/{name}", "--for=condition=complete", "--timeout=3h")),
        Step("logs", (*context, "logs", f"job/{name}", "--tail=-1")),
        Step("apply-collector", (*context, "apply", "-f", str(local / "collector.yaml"))),
        Step(
            "wait-collector",
            (*context, "wait", f"pod/{collector}", "--for=condition=ready", "--timeout=5m"),
        ),
        Step("copy-out", (*context, "cp", f"{collector}:{OUTPUT_SUBPATH}/{cell.id}", str(local))),
        Step("delete-collector", (*context, "delete", "pod", collector, "--ignore-not-found")),
        Step("delete", (*context, "delete", "job", name, "--ignore-not-found")),
    )


CPU_OVERLAY = "deploy/kubernetes/overlays/inference"
CUDA_OVERLAY = "deploy/kubernetes/overlays/inference-cuda"
LAN_OVERLAY = "deploy/kubernetes/overlays/inference-lan"
LAN_SERVICE = "inference-lan"
INFERENCE_PORT = 8092
# The override. Set it to pin the address the NAS cells call; leave it unset and
# the runner reads it off the LoadBalancer the `inference-lan` overlay asks for.
INFERENCE_ENV = "MATRIX_INFERENCE_BASE_URL"
DERIVED_ADDRESS = "<derived at run time>"

KUBECTL = ("kubectl", "--context", "$MATRIX_K8S_CONTEXT", "-n", "$MATRIX_K8S_NAMESPACE")
# `.ip` on most controllers, `.hostname` on the ones that hand out a name.
LB_ADDRESS_PATH = "{.status.loadBalancer.ingress[0].ip}"


def overlay_path(device: str) -> str:
    """Which overlay directory a device choice applies. `auto` is resolved before this."""
    if device not in {"cpu", "cuda"}:
        raise PlanError(f"inference device must be cpu or cuda by now, got {device!r}")
    return CPU_OVERLAY if device == "cpu" else CUDA_OVERLAY


def needs_lan_address(cells: tuple[Cell, ...]) -> bool:
    """Whether anything off the cluster has to call the inference service.

    Only the NAS lane does. A cluster cell reaches it at `http://inference:8092`
    over the cluster's own DNS and needs no address handed out to the LAN.
    """
    return any(cell.lane == "nas" and cell.facts == "service" for cell in cells)


def inference_overlay_steps(*, device: str, keep: bool, lan: bool) -> tuple[Step, ...]:
    """Bring the inference service up before the service cells, and take it down after.

    `auto` is resolved at run time by asking the cluster whether any node carries
    the GPU operator's label. A dry run prints the probe instead of its answer,
    because the answer depends on a cluster the transcript should not assume.

    `lan` adds the second Service that asks for a LoadBalancer address, which is
    the only way a NAS outside the cluster can reach the port. The address is read
    back rather than configured: it is whatever the controller hands out, and it
    is never written to a file in the repo.
    """
    probe = Step(
        "probe-gpu", (*KUBECTL, "get", "nodes", "-l", "nvidia.com/gpu.present=true", "-o", "name")
    )
    overlay = "<cpu or cuda, decided by probe-gpu>" if device == "auto" else overlay_path(device)
    steps = [
        probe
        if device == "auto"
        else Step("device", ("echo", f"inference device pinned: {device}")),
        Step("apply-inference", (*KUBECTL, "apply", "-k", overlay)),
        Step(
            "wait-inference",
            (
                *KUBECTL,
                "rollout",
                "status",
                "deployment/immich-memories-inference",
                "--timeout=10m",
            ),
        ),
    ]
    if lan:
        steps += [
            Step("apply-inference-lan", (*KUBECTL, "apply", "-k", LAN_OVERLAY)),
            Step(
                "read-lan-address",
                (*KUBECTL, "get", "service", LAN_SERVICE, "-o", f"jsonpath={LB_ADDRESS_PATH}"),
            ),
            Step("derive", ("echo", f"{INFERENCE_ENV}={DERIVED_ADDRESS}")),
        ]
    if not keep:
        if lan:
            steps.append(
                Step(
                    "delete-inference-lan",
                    (*KUBECTL, "delete", "-k", LAN_OVERLAY, "--ignore-not-found"),
                )
            )
        steps.append(
            Step("delete-inference", (*KUBECTL, "delete", "-k", overlay, "--ignore-not-found"))
        )
    return tuple(steps)


def _k8s_manifests(
    cell: Cell, memory: dict, month: str, image: str, config_yaml: str
) -> dict[str, str]:
    """A ConfigMap with the pinned config, a Job that mounts it, and the collector.

    Built from `deploy/kubernetes/base/job.yaml`: same securityContext, same model
    and output claims. The command is the same container script the NAS runs, so a
    cluster cell reports peak memory the way a NAS cell does.
    """
    name = f"setup-matrix-{cell.id}"
    config_block = "\n".join(f"    {line}" for line in config_yaml.splitlines())
    script = "\n".join(
        f"                {part}" for part in _container_script(cell, memory, month).split("; ")
    )
    _, credentials = _variables(cell.config)
    secret_block = (
        f"          envFrom:\n            - secretRef:\n                name: {name}-secrets\n"
        if credentials
        else ""
    )
    return {
        "configmap.yaml": _CONFIGMAP.format(name=name, config=config_block),
        "job.yaml": _JOB.format(
            name=name,
            image=image,
            script=script,
            config=REMOTE_CONFIG,
            out=REMOTE_OUT,
            secrets=secret_block,
            subpath=f"{OUTPUT_SUBPATH}/{cell.id}",
        ),
        "collector.yaml": _COLLECTOR.format(name=f"{name}-collect", image=image, out=REMOTE_OUT),
    }


_CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: {name}-config
  namespace: $MATRIX_K8S_NAMESPACE
data:
  config.yaml: |
{config}
"""

_JOB = """apiVersion: batch/v1
kind: Job
metadata:
  name: {name}
  namespace: $MATRIX_K8S_NAMESPACE
  labels:
    app.kubernetes.io/name: immich-memories
    app.kubernetes.io/component: setup-matrix
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: generate
          image: {image}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          command:
            - /bin/bash
            - -lc
            - |
{script}
{secrets}          env:
            - name: IMMICH_MEMORIES_TRIAGE__ENCODER
              value: /models/triage/dinov2-small.onnx
            - name: IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR
              value: /models/huggingface
          volumeMounts:
            - name: config
              mountPath: {config}
              subPath: config.yaml
            - name: output
              mountPath: {out}
              subPath: {subpath}
            - name: models
              mountPath: /models
          resources:
            requests:
              memory: "4Gi"
              cpu: "2000m"
            limits:
              memory: "16Gi"
              cpu: "8000m"
      volumes:
        - name: config
          configMap:
            name: {name}-config
        - name: output
          persistentVolumeClaim:
            claimName: immich-memories-output
        - name: models
          persistentVolumeClaim:
            claimName: immich-memories-models
"""

# Mounts the output claim and does nothing, so `kubectl cp` has a running
# container to shell into after the Job's own pod has finished.
_COLLECTOR = """apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: $MATRIX_K8S_NAMESPACE
  labels:
    app.kubernetes.io/name: immich-memories
    app.kubernetes.io/component: setup-matrix-collector
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: collect
      image: {image}
      command: ["sleep", "1800"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop:
            - ALL
      volumeMounts:
        - name: output
          mountPath: {out}
      resources:
        requests:
          memory: "64Mi"
          cpu: "50m"
        limits:
          memory: "256Mi"
          cpu: "500m"
  volumes:
    - name: output
      persistentVolumeClaim:
        claimName: immich-memories-output
"""


def build_cell_plan(
    cell: Cell,
    *,
    manifest: dict,
    library: dict,
    month: str,
    out_dir: Path,
    image: str,
    environment: dict[str, str],
) -> CellPlan:
    """One cell's commands, manifests and pins, with no value from `environment` inside."""
    pins = _pins_for(cell, manifest, library)
    source = {**(library.get("config") or {}), **cell.config}
    references, credentials = _variables(source)
    required = tuple(dict.fromkeys((*cell.requires_env, *references, *credentials)))
    missing = [name for name in required if not (environment.get(name) or "").strip()]
    skip = f"needs {', '.join(missing)}, absent from the loaded environment" if missing else None

    config_yaml = yaml.safe_dump(nested(pins), sort_keys=False)
    memory = manifest.get("memory") or {}
    manifests: dict[str, str] = {}
    if cell.lane == "mac":
        steps = _mac_steps(cell, memory, month, out_dir)
        cache = "$HOME/.immich-memories/cache"
    elif cell.lane == "nas":
        steps = _nas_steps(cell, memory, month, out_dir, image)
        cache = REMOTE_CACHE
    elif cell.lane == "k8s":
        steps = _k8s_steps(cell, out_dir)
        manifests = _k8s_manifests(cell, memory, month, image, config_yaml)
        cache = REMOTE_CACHE
    else:
        raise PlanError(f"{cell.id}: unknown lane {cell.lane!r}")

    return CellPlan(
        cell=cell,
        pins=pins,
        config_yaml=config_yaml,
        steps=steps,
        manifests=manifests,
        app_credentials=credentials,
        cache_dir=cache,
        skip_reason=skip,
    )


def nested(pins: dict[str, Any]) -> dict[str, Any]:
    """Dotted pins as the nested mapping a config file holds."""
    out: dict[str, Any] = {}
    for dotted, value in sorted(pins.items()):
        *branches, leaf = dotted.split(".")
        target = out
        for branch in branches:
            target = target.setdefault(branch, {})
        target[leaf] = value
    return out


def build_plan(
    *,
    manifest: dict,
    library: str,
    month: str | None,
    lanes: tuple[str, ...],
    cell_ids: tuple[str, ...],
    out_dir: Path,
    image: str,
    environment: dict[str, str],
) -> Plan:
    """The whole request as a plan, skipped cells included."""
    libraries = manifest.get("libraries") or {}
    if library not in libraries:
        raise PlanError(f"unknown library {library!r}; the manifest knows {sorted(libraries)}")
    settings = libraries[library]
    resolved = str(month or settings.get("month") or "")
    if not resolved:
        raise PlanError(f"--month is required for library {library!r}")
    month_parts(resolved)

    known = read_cells(manifest)
    unknown = set(cell_ids) - {cell.id for cell in known}
    if unknown:
        raise PlanError(f"unknown cell(s): {', '.join(sorted(unknown))}")
    chosen = [
        cell
        for cell in known
        if (not lanes or cell.lane in lanes) and (not cell_ids or cell.id in cell_ids)
    ]
    if not chosen:
        raise PlanError("no cell matches the requested lanes and ids")

    return Plan(
        library=library,
        month=resolved,
        image=image,
        anonymize_required=bool(settings.get("anonymize_required")),
        cells=tuple(
            build_cell_plan(
                cell,
                manifest=manifest,
                library=settings,
                month=resolved,
                out_dir=out_dir,
                image=image,
                environment=environment,
            )
            for cell in chosen
        ),
        environment=dict(environment),
    )


def dry_run_text(plan: Plan, *, overlay: tuple[Step, ...] = ()) -> str:
    """The plan as a transcript: every command and manifest, no value from the environment."""
    lines = [
        f"setup matrix: library {plan.library}, month {plan.month}, image {plan.image}",
        f"{len(plan.cells)} cells, {len(plan.runnable)} runnable, {len(plan.skipped)} skipped",
        f"anonymisation {'REQUIRED' if plan.anonymize_required else 'not required'} for this library",
    ]
    if overlay:
        lines += ["", "# inference service overlay"]
        lines += [f"  {step.name:<18} {step}" for step in overlay]
    for item in plan.cells:
        cell = item.cell
        lines += [
            "",
            f"## {cell.id}  lane={cell.lane} reader={cell.reader} "
            f"facts={cell.facts} tier={cell.tier}",
            f"   {cell.why}",
        ]
        if item.skip_reason:
            lines.append(f"   SKIP: {item.skip_reason}")
            continue
        if item.app_credentials:
            lines.append(
                "   credentials in the process env: "
                + ", ".join(f"{name}=<masked>" for name in item.app_credentials)
            )
        lines.append("   pinned config:")
        lines += [f"     {line}" for line in item.config_yaml.rstrip().splitlines()]
        lines += [f"   {step.name:<18} {step}" for step in item.steps]
        for name, body in item.manifests.items():
            lines.append(f"   manifest {name}:")
            lines += [f"     {line}" for line in body.rstrip().splitlines()]
    if plan.skipped:
        lines += ["", "skipped: " + ", ".join(item.cell.id for item in plan.skipped)]
    return "\n".join(lines) + "\n"


def _plain(part: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_@%+=:,./$-]+", part))
