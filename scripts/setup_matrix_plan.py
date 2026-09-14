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
from matrix_pinned_config import DROP, hosted_reader_pins, remote_path_pins

MANIFEST = Path(__file__).resolve().parent / "setup_matrix.yaml"
SCHEMA = "setup-matrix-v1"

# `$env:NAME` is resolved by the runner. `${NAME}` is left for the app to expand.
_ENV_REFERENCE = re.compile(r"^\$env:([A-Z][A-Z0-9_]*)$")
_APP_REFERENCE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")

# Where the NAS lane is told to connect. This is an ssh DESTINATION — what ssh
# and nothing else would accept as `[user@]host`, ideally a Host alias out of
# ~/.ssh/config carrying the key, the user, BatchMode and ConnectTimeout.
NAS_SSH_ENV = "MATRIX_NAS_SSH"

# What the NAS container is capped at, rendered verbatim into its `docker run`.
# The default is a CFS quota; a kernel built without the CFS bandwidth controller
# — a Synology on cgroup v1 — refuses it with "NanoCPUs can not be set", and there
# `--cpuset-cpus 0-3 --memory 4g` pins the same four cores instead.
NAS_LIMITS_ENV = "MATRIX_NAS_DOCKER_LIMITS"
DEFAULT_NAS_LIMITS = "--cpus 4 --memory 4g"
# The value reaches a remote shell as part of a command docker runs as root, so
# only caps go through: a flag that is not one of these is refused, not passed on.
_LIMIT_FLAGS = frozenset(
    {
        "--cpus",
        "--cpuset-cpus",
        "--cpuset-mems",
        "--cpu-shares",
        "--memory",
        "--memory-reservation",
        "--memory-swap",
        "--memory-swappiness",
    }
)
_LIMIT_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,:_-]*")

# The fixture library binds here so the NAS and the cluster can reach the Mac.
FIXTURE_PORT = 8078
FIXTURE_ENV = "MATRIX_FIXTURE_BASE_URL"

# One container filesystem layout, shared by both remote lanes so the capture
# code has a single set of paths to read back.
REMOTE_CONFIG = "/out/config.yaml"
REMOTE_OUT = "/out"
# Every cell gets an editorial cache of its own, and the model files stay shared
# at /models. The bank remembers what a reader decided and what a caption cost,
# so a cell running second on a shared cache reports the cell before it: the
# first Mac run had `mac-rules` culling pictures with the model's own words and
# calling a one-second re-read a cold preparation.
REMOTE_CACHE = "/cache"
CELL_CACHE_DIR = "cache"
# The shared models volume, and every pinned artifact placed under it by name.
# Pinned rather than left to HOME: the cluster Job's HOME is /home/immich, a path
# in the pod's own layer, so the first real run had the encoder on the volume
# (pinned by env), the Hugging Face cache on the volume (same), and the Marqo
# export under /home/immich — three roots, one of them gone with the pod, and
# nothing fetching any of them. One root, named in the config both lanes read.
REMOTE_MODELS = "/models"
MODELS_FETCH_LOG = "models-fetch.log"
MODELS_FETCH_SECONDS = "models-fetch-seconds.txt"
REMOTE_LANES = frozenset({"nas", "k8s"})
# The two mounts of the cluster's one data claim: models shared, cache per cell.
MODELS_SUBPATH = "models"
CACHE_SUBPATH = "cache"
# Where a cluster cell writes inside the shared output claim, so the collector
# pod and the Job agree on one path.
OUTPUT_SUBPATH = "setup-matrix"

# The matrix's own claims. It never mounts the app's: those are RWO and stay
# attached to the running Deployment on whichever node holds it, so a Job that
# asked for them would sit in Multi-Attach forever, and `-models` does not exist
# at all in a namespace that predates deploy/kubernetes/base/pvc.yaml.
DATA_CLAIM = "setup-matrix-data"
OUTPUT_CLAIM = "setup-matrix-output"

PREPARE_COLD_LOG = "prepare-cold.log"
PREPARE_WARM_LOG = "prepare-warm.log"
GENERATE_LOG = "generate.log"
PEAK_RSS_FILE = "peak-rss-bytes.txt"
CPU_FILE = "cpu.txt"

# Where a memory's attempts live inside the editorial cache, and where a remote
# cell copies its own so the capture can read them. The cache itself never comes
# back (it is previews and thumbnails by the gigabyte), so without this copy the
# cut, the trace and the projection stay on the volume and the row publishes an
# empty `selected_asset_ids` beside a film that plainly has pictures in it.
EDITORIAL_RUNS = "editorial-runs"
REMOTE_ATTEMPTS = "attempts"

# Whether this cell's cache already held a run. Its directory being there proves
# nothing: the kubelet creates the subPath before the container starts and the
# NAS lane's own `mkdir -p` creates it a moment before the run. A file the app
# never writes does prove it, so the container leaves one on its way through.
CACHE_MARKER = ".setup-matrix-cell"
CACHE_PRIMED_FILE = "cache-primed.txt"
PRIMED = "primed"
COLD = "cold"
# `--fresh-cache` empties the cell's own bank before it prepares, so a cold
# preparation is a first derivation rather than a replay of what the last run
# banked. The cluster is the lane that needs a switch: its cache is a subPath
# mount, so only the container can empty it, and the Job carries this variable
# when the run asked for it. The NAS empties its own cache directory over ssh,
# before docker binds it, and the Mac's is a plain directory beside the logs.
FRESH_CACHE_ENV = "MATRIX_FRESH_CACHE"
# `*` alone leaves the marker file behind, and the cell would then call itself
# primed one line after being emptied. An unmatched glob reaches `rm -f` as its
# own text, which is a path that does not exist and so nothing at all.
_WIPE_GLOBS = ("*", ".[!.]*")
# The NAS lane looks for itself, in the one step that runs before the directory
# it would be looking for exists. Read back out of this step's stdout.
MAKE_REMOTE_DIR = "make-remote-dir"
# The Mac lane's wipe is a step of its own, so `--dry-run --fresh-cache` shows
# the directory that is about to go.
FRESH_CACHE = "fresh-cache"
# The step the runner retries and then judges on the film being here.
COPY_OUT = "copy-out"

# The env var `config_loader` maps to `immich.api_key`, which is how a cluster
# cell is handed the operator's key without a ConfigMap ever holding one.
IMMICH_KEY_ENV = "IMMICH_MEMORIES_IMMICH__API_KEY"
# What a rendered plan prints where a value out of the operator's own config
# goes. The runner puts the value there at the moment the command runs, so the
# dry run, the manifests it writes and every log carry the words instead.
FROM_OPERATOR_CONFIG = "<from operator config>"


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
    """One command in a cell's sequence, rendered with variable names not values.

    `pipe_to` is a second command fed from the first one's stdout. It stays two
    argv lists rather than becoming a shell string so nothing here has to be
    quoted for a shell that never runs.
    """

    name: str
    command: tuple[str, ...]
    pipe_to: tuple[str, ...] = ()

    def __str__(self) -> str:
        """The command as a shell would have to be given it.

        `shlex.quote` rather than wrapping in quotes: the NAS step already carries
        a quoted script inside its argument, and single quotes do not nest. Naive
        wrapping printed a line that looked runnable and was not.
        """
        rendered = _render(self.command)
        return f"{rendered} | {_render(self.pipe_to)}" if self.pipe_to else rendered


@dataclass(frozen=True)
class CellPlan:
    """What one cell will do, as text a person can read and a shell could run.

    `diagnostics` are not part of the sequence: they are what to ask when a step
    in it gives up, so a cell that failed says why in its own record.
    """

    cell: Cell
    pins: dict[str, Any]
    config_yaml: str
    steps: tuple[Step, ...]
    manifests: dict[str, str]
    app_credentials: tuple[str, ...]
    cache_dir: str
    # Whether this cell's Immich comes out of the operator's own config rather
    # than the manifest. Only a cluster cell ever needs it: the other two lanes
    # are handed a copy of that config and read it there.
    operator_immich: bool = False
    # Whether this cell empties its own bank before it prepares.
    fresh_cache: bool = False
    # Only a NAS cell has any: the other two lanes are not capped by a container.
    container_limits: str = ""
    skip_reason: str | None = None
    diagnostics: tuple[Step, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Every cell the request asked for, with what each needs and what each will run."""

    library: str
    month: str
    image: str
    anonymize_required: bool
    cells: tuple[CellPlan, ...]
    environment: dict[str, str] = field(default_factory=dict)
    # `url` and `api_key` as the operator's own config holds them, filled in by
    # the runner the moment before it executes and never by `build_plan`: a
    # printed plan has to be readable without a config on the machine reading it.
    operator_credentials: dict[str, str] = field(default_factory=dict)

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


def library_pins_immich(library: dict) -> bool:
    """Whether the manifest gives this library an Immich of its own.

    `demo` names the fixture server and a fake key. A real library names neither
    and runs against the operator's, which every lane but the cluster gets in the
    copied config: the Job reads a ConfigMap built from the pins alone.
    """
    return bool((library.get("config") or {}).keys() & {"immich.url", "immich.api_key"})


def cache_pins(cache_dir: str) -> dict[str, str]:
    """The three settings that decide where a cell banks, pointed at one directory.

    `editorial.annotation_database` is pinned blank on purpose: blank is what
    resolves the bank under the cache directory, and an operator's own absolute
    override would quietly put every cell back in one bank.
    """
    return {
        "cache.directory": cache_dir,
        "cache.database": f"{cache_dir}/cache.db",
        "editorial.annotation_database": "",
    }


def fetches_models(cell: Cell) -> bool:
    """Whether this cell's container has to acquire the pinned models itself.

    The Mac lane runs on an install that fetched them long ago. A remote container
    starts against a models volume that is empty until some cell fills it, and a
    cell taking its picture facts off the service runs no local model at all.
    """
    return cell.lane in REMOTE_LANES and cell.facts == "local"


def _pins_for(cell: Cell, manifest: dict, library: dict) -> dict[str, Any]:
    pins: dict[str, Any] = dict(manifest.get("baseline_config") or {})
    pins.update(library.get("config") or {})
    pins["editorial.preparation.tier"] = cell.tier
    if cell.hosted:
        pins.update(hosted_reader_pins())
    pins.update(cell.config)
    if cell.lane in REMOTE_LANES:
        pins.update(remote_path_pins(out=REMOTE_OUT, models=REMOTE_MODELS))
    if fetches_models(cell):
        # `models fetch` warms every pinned artifact first, so this only covers
        # what a pinned snapshot adds between the release and the run. Where the
        # files land is `remote_path_pins`, which every remote cell gets.
        pins["editorial.preparation.allow_model_downloads"] = True
    return {key: _render_pin(value) for key, value in pins.items()}


def _scope(month: str) -> list[str]:
    year, number = month_parts(month)
    return ["--year", year, "--month", number]


def _mac_steps(
    cell: Cell, memory: dict, month: str, out_dir: Path, *, fresh: bool
) -> tuple[Step, ...]:
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
    # The whole directory, because the Mac's cache is the runner's own to make:
    # nothing is mounted on it and `prepare` creates it again on its way past.
    wipe = (Step(FRESH_CACHE, ("rm", "-rf", str(cell_dir / CELL_CACHE_DIR))),) if fresh else ()
    return (
        *wipe,
        Step("prepare-cold", tuple(prepare)),
        Step("prepare-warm", tuple(prepare)),
        Step("generate", tuple(generate)),
    )


def _models_fetch_lines(root: str) -> list[str]:
    """Acquire the pinned artifacts, on the container's own stopwatch.

    Its own phase, never folded into the cold preparation: on a new models volume
    this is a download of hundreds of megabytes, and charging that to preparation
    would publish a number about someone's network as if it were about a NAS.
    """
    return [
        "models_started=$SECONDS",
        f"{root} models fetch 2>&1 | tee {REMOTE_OUT}/{MODELS_FETCH_LOG}",
        f"echo $((SECONDS - models_started)) > {REMOTE_OUT}/{MODELS_FETCH_SECONDS}",
    ]


def _cache_marker_lines() -> list[str]:
    """Record whether this cell's cache already held a run, then claim it for this one."""
    primed = f"{REMOTE_OUT}/{CACHE_PRIMED_FILE}"
    return [
        f"[ -e {REMOTE_CACHE}/{CACHE_MARKER} ] && echo {PRIMED} > {primed} "
        f"|| echo {COLD} > {primed}",
        f"touch {REMOTE_CACHE}/{CACHE_MARKER}",
    ]


def _wipe_cache(directory: str) -> str:
    """Empty an editorial cache, dotfiles included, without removing the directory itself.

    The directory stays because something is mounted on it: a bind on the NAS, a
    subPath on the cluster. `/models` is never named here — the model files are
    shared by the whole matrix and re-fetching them measures a network, not a setup.
    """
    return "rm -rf " + " ".join(f"{directory}/{glob}" for glob in _WIPE_GLOBS)


def _fresh_cache_lines(*, fresh: bool) -> list[str]:
    """Empty this cell's bank, on the switch the Job carries.

    Only the cluster's cells come through here. A cluster cell's cache is a
    subPath mount, so the container is the only thing that can empty it, and the
    Job says so in its own `env` block. The NAS cell's cache is a directory on
    the NAS, emptied over ssh before docker ever binds it.
    """
    if not fresh:
        return []
    return [f'[ "${{{FRESH_CACHE_ENV}:-0}}" = 1 ] && {_wipe_cache(REMOTE_CACHE)} || true']


def _readable_lines(memory_key: str) -> list[str]:
    """Let the user who pulls the results read what the container wrote as root.

    The NAS container runs `--user 0:0`, because the share is mounted with an
    ownership the image's uid 1000 cannot write to. Everything it leaves behind
    belongs to root, and `cp -a` carries the app's own 0700 across onto the
    attempt directory: `pull-results` exited 2 on
    `tar: ./attempts/<cell>: Cannot open: Permission denied`, and the cell
    published an empty cut beside a film that had come back intact.

    Named paths rather than `/out`, because on the NAS that directory also holds
    the credentials file and the cell's own cache, and neither is opened up.
    """
    targets = " ".join(
        [
            f"{REMOTE_OUT}/{REMOTE_ATTEMPTS}",
            f"{REMOTE_OUT}/*.txt",
            f"{REMOTE_OUT}/*.log",
            f"{REMOTE_OUT}/*.mp4",
            f"{REMOTE_OUT}/{memory_key}_*",
        ]
    )
    return [f"chmod -R a+rX {targets} 2>/dev/null || true"]


def _copy_attempt_lines(memory_key: str) -> list[str]:
    """Bring this memory's attempts out of the cache and into what the copy-out pulls back.

    Only this memory's directory. The bank beside it is what every producer ever
    derived for the library, which is gigabytes and means nothing off the host,
    and `editorial-runs/by-run` indexes every memory the cache has ever seen.
    """
    return [
        f"mkdir -p {REMOTE_OUT}/{REMOTE_ATTEMPTS}",
        f"cp -a {REMOTE_CACHE}/{EDITORIAL_RUNS}/{memory_key} "
        f"{REMOTE_OUT}/{REMOTE_ATTEMPTS}/ 2>/dev/null || true",
    ]


def _container_script(cell: Cell, memory: dict, month: str, *, fresh: bool = False) -> str:
    """What a remote container runs: fetch, prepare twice, cut, then report its own cost.

    The published image is `python:3.11-slim` underneath, which carries no
    `/usr/bin/time`. The kernel already counts what we want, so the script reads
    the cgroup files on the way out and writes them beside the video. Both cgroup
    generations are tried because DSM and the cluster do not agree on which one
    they are on; when neither answers, the capture records the field as unmeasured
    rather than guessing.

    The attempt is copied after `rc` has been taken, not before: `$PIPESTATUS`
    describes the last pipeline that ran, and anything between the cut and that
    read would be reporting its own exit code as the run's. The last thing it
    does is make what it wrote readable to whoever comes to collect it.
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
            *_fresh_cache_lines(fresh=fresh),
            *_cache_marker_lines(),
            *(_models_fetch_lines(root) if fetches_models(cell) else []),
            f"{root} prepare {scope} 2>&1 | tee {REMOTE_OUT}/{PREPARE_COLD_LOG}",
            f"{root} prepare {scope} 2>&1 | tee {REMOTE_OUT}/{PREPARE_WARM_LOG}",
            f"{cut} 2>&1 | tee {REMOTE_OUT}/{GENERATE_LOG}",
            "rc=${PIPESTATUS[0]}",
            *_copy_attempt_lines(cell.id),
            f"cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory/memory.max_usage_in_bytes "
            f"2>/dev/null | head -1 > {REMOTE_OUT}/{PEAK_RSS_FILE}",
            f"cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/cpuacct/cpuacct.usage "
            f"2>/dev/null > {REMOTE_OUT}/{CPU_FILE}",
            *_readable_lines(cell.id),
            "exit $rc",
        ]
    )


def _credential_flags(remote: str, credentials: tuple[str, ...]) -> list[str]:
    """`--env-file`, because a bare `-e NAME` on the far side of ssh sends an empty value.

    docker fills `-e NAME` from the environment of the shell running it, and a
    non-interactive ssh session carries none of the runner's variables. Both NAS
    hosted cells reached their provider with an empty key: Melious answered 401
    while the same key worked from the cluster, where the runner makes a Secret
    out of its own environment. `push-env` writes the file 0600, `pull-results`
    leaves it behind and `drop-credentials` removes it.
    """
    return ["--env-file", f"{remote}/env"] if credentials else []


def _push_env_steps(remote: str, credentials: tuple[str, ...]) -> tuple[Step, ...]:
    """The cell's credentials into a file on the NAS, with no copy on this machine.

    A value exists in the argv of the local `printf` for as long as it runs,
    which is the exposure `kubectl create secret --from-literal` already accepts
    on the other lane. It never reaches the NAS's command line, a file here, or a
    log: the step writes through a pipe and its own stdout is empty. The rendered
    plan carries `NAME=$NAME`, so a dry run can go in a pull request.
    """
    if not credentials:
        return ()
    return (
        Step(
            "push-env",
            ("printf", "%s\\n", *[f"{name}=${name}" for name in credentials]),
            pipe_to=("ssh", "$MATRIX_NAS_SSH", f"umask 077 && cat > {remote}/env"),
        ),
    )


def nas_docker_limits(environment: dict[str, str]) -> tuple[str, ...]:
    """The NAS container's resource flags, as argv, checked flag by flag.

    Each entry has to be a cap and a plain value, because the result is rendered
    into a command a remote shell re-splits: a `;` or a `-v` in there would be
    running on the NAS as root.
    """
    raw = (environment.get(NAS_LIMITS_ENV) or "").strip() or DEFAULT_NAS_LIMITS
    tokens: list[str] = []
    for part in shlex.split(raw):
        flag, joined, value = part.partition("=")
        tokens.extend([flag, value] if joined else [part])
    if len(tokens) % 2:
        raise PlanError(f"{NAS_LIMITS_ENV}: {tokens[-1]} has no value")
    for flag, value in zip(tokens[::2], tokens[1::2], strict=True):
        if flag not in _LIMIT_FLAGS:
            raise PlanError(
                f"{NAS_LIMITS_ENV}: {flag} is not a container resource flag. It holds the "
                f"cell's caps and nothing else: {', '.join(sorted(_LIMIT_FLAGS))}."
            )
        if not _LIMIT_VALUE.fullmatch(value):
            raise PlanError(f"{NAS_LIMITS_ENV}: {value!r} is not a value for {flag}")
    return tuple(tokens)


def _nas_cache_probe(remote: str, *, fresh: bool) -> str:
    """What the first ssh says about this cell's cache, and what it does to it.

    The look has to come before the `mkdir -p`, which is what would make the
    answer "yes" on every run after the first line of it. A run that asked for a
    fresh cache does not look at all: it empties the directory and the answer is
    cold because this step just made it so.
    """
    cache = f"{remote}/{CELL_CACHE_DIR}"
    if fresh:
        return f"{_wipe_cache(cache)}; echo {COLD}; "
    return f"test -d {cache} && echo {PRIMED} || echo {COLD}; "


def _nas_steps(
    cell: Cell,
    memory: dict,
    month: str,
    out_dir: Path,
    image: str,
    limits: tuple[str, ...],
    *,
    fresh: bool,
) -> tuple[Step, ...]:
    """ssh, one docker run, and the results tarred back.

    WHY the single quoted string: `ssh host a b c` concatenates its arguments and
    hands the result to the remote shell, which re-splits them. Passing the docker
    command as separate argv entries would lose the grouping of `-lc <script>` and
    the remote bash would try to run the script's first word as a command.
    """
    remote = f"$MATRIX_NAS_OUT/{cell.id}"
    local = out_dir / cell.id
    _, credentials = _variables(cell.config)
    docker = [
        "$MATRIX_NAS_DOCKER",
        "run",
        "--rm",
        *limits,
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
        "-v",
        f"{remote}/{CELL_CACHE_DIR}:{REMOTE_CACHE}",
        *_credential_flags(remote, credentials),
        image,
        "/bin/bash",
        "-lc",
        shlex.quote(_container_script(cell, memory, month)),
    ]
    # WHY tar over ssh instead of scp: the NAS runs an OpenSSH 8.2 server with
    # the SFTP subsystem turned off, and a modern scp client speaks SFTP by
    # default, so every copy died with "Connection closed". `scp -O` would also
    # work; tar needs nothing of the remote but a shell, which is the one thing
    # the ssh destination is guaranteed to give us.
    # Both directories, because docker creates neither: a bind mount of a path
    # that is not there is "Bind mount failed: ... does not exist", and the whole
    # cell dies before the container starts.
    return (
        Step(
            MAKE_REMOTE_DIR,
            (
                "ssh",
                "$MATRIX_NAS_SSH",
                _nas_cache_probe(remote, fresh=fresh)
                + f"mkdir -p {remote} {remote}/{CELL_CACHE_DIR}",
            ),
        ),
        Step(
            "push-config",
            ("tar", "-C", str(local), "-cf", "-", "config.yaml"),
            # WHY the umask: the file is a copy of the operator's config, key
            # included, and it is landing on a NAS whose shares other people
            # mount. It leaves this machine 0600 and tar carries the mode, but
            # only where the far side restores permissions from the archive --
            # this is the half that does not depend on which tar the NAS has.
            pipe_to=(
                "ssh",
                "$MATRIX_NAS_SSH",
                f"umask 077 && mkdir -p {remote} && tar -C {remote} -xf -",
            ),
        ),
        *_push_env_steps(remote, credentials),
        Step("run", ("ssh", "$MATRIX_NAS_SSH", " ".join(docker))),
        # The cell's cache is not pulled: it is previews and thumbnails by the
        # gigabyte, it means nothing off the NAS, and leaving it there is what
        # makes a re-run of this cell warm.
        Step(
            "pull-results",
            (
                "ssh",
                "$MATRIX_NAS_SSH",
                f"tar -C {remote} --exclude=./{CELL_CACHE_DIR} --exclude=./env "
                f"--exclude=./config.yaml -cf - .",
            ),
            pipe_to=("tar", "-C", str(local), "-xf", "-"),
        ),
        # The credentials leave the NAS with the cell, whether or not it worked,
        # and the pinned config is one of them: it is the operator's own file
        # with the pins over the top. Not pulled back either -- this machine
        # wrote it, and a local tar would extract it under the operator's umask
        # rather than the 0600 it was written at.
        Step(
            "drop-credentials",
            ("ssh", "$MATRIX_NAS_SSH", f"rm -f {remote}/env {remote}/config.yaml"),
        ),
    )


def _secret_steps(
    cell: Cell, context: tuple[str, ...], *, operator_immich: bool
) -> tuple[Step, ...]:
    """Put the cell's credentials in a Secret without ever writing one to a file.

    The value only exists in the argv of the create call, substituted the moment
    it runs. Delete-then-create rather than `apply` because `create secret` has no
    idempotent form that does not need a shell pipe.

    The operator's Immich key joins the hosted ones here rather than in the
    ConfigMap, which is a file on the cluster that anyone who can read the
    namespace can read.
    """
    _, credentials = _variables(cell.config)
    literals = [f"--from-literal={variable}=${variable}" for variable in credentials]
    if operator_immich:
        literals.append(f"--from-literal={IMMICH_KEY_ENV}={FROM_OPERATOR_CONFIG}")
    if not literals:
        return ()
    name = f"setup-matrix-{cell.id}-secrets"
    return (
        Step("drop-secret", (*context, "delete", "secret", name, "--ignore-not-found")),
        Step("make-secret", (*context, "create", "secret", "generic", name, *literals)),
    )


def _k8s_steps(cell: Cell, out_dir: Path, *, operator_immich: bool) -> tuple[Step, ...]:
    """Claims, apply, wait to be scheduled, wait to finish, copy out, tear down.

    WHY a collector: `kubectl cp` shells into the pod to run tar, and a finished
    Job's pod has no running container to shell into. The collector mounts the
    same output claim, stays up while the copy happens, and is deleted after.

    WHY two waits: a pod that cannot be scheduled, for a missing claim or for a
    node with no room, is Pending and never reaches `complete`. One wait meant
    three hours of watching a pod that was never going to start.

    WHY the drops: a Job's `spec.template` is immutable, so `apply` over one an
    interrupted run left behind is rejected outright — "field is immutable" — and
    a leftover collector Pod is the same story. Every cell starts from nothing.
    """
    context = KUBECTL
    name = f"setup-matrix-{cell.id}"
    collector = f"{name}-collect"
    local = out_dir / cell.id
    return (
        Step(
            "drop-job",
            (*context, "delete", f"job/{name}", f"configmap/{name}-config", *DELETE_FLAGS),
        ),
        # WHY a bounded delete and not `kubectl wait --for=delete`: wait has no
        # --ignore-not-found, and "nothing matches" — the healthy case — is an
        # error to it. This waits for the same thing and also clears a pod some
        # other cell's interrupted run left holding the ReadWriteOnce claim,
        # which is what had `k8s-hosted-zai` Pending with no events at all.
        Step(
            "drain-pods",
            (*context, "delete", "pod", "-l", MATRIX_POD_LABEL, *DELETE_FLAGS, DRAIN_TIMEOUT),
        ),
        *_secret_steps(cell, context, operator_immich=operator_immich),
        Step("apply-claims", (*context, "apply", "-f", str(local / "claims.yaml"))),
        Step(
            "apply",
            (*context, "apply", "-f", str(local / "configmap.yaml"), "-f", str(local / "job.yaml")),
        ),
        # WHY before the scheduling wait: the controller creates the pod a moment
        # after `apply` returns, and `kubectl wait` on a selector that matches
        # nothing is an error rather than a wait. The runner polls this one until
        # it names a pod, which is what the wait below then has to wait on.
        Step("wait-created", (*context, "get", "pod", "-l", f"job-name={name}", "-o", "name")),
        Step(
            "wait-scheduled",
            (
                *context,
                "wait",
                "pod",
                "-l",
                f"job-name={name}",
                "--for=condition=PodScheduled",
                f"--timeout={SCHEDULING_TIMEOUT}",
            ),
        ),
        # WHY a status query rather than `kubectl wait --for=condition=complete`:
        # a Job that FAILED never satisfies that wait, and with backoffLimit 0 it
        # is a likely outcome. The runner polls this until one counter answers,
        # under the same three-hour ceiling.
        Step("wait", (*context, "get", f"job/{name}", "-o", f"jsonpath={JOB_STATUS_PATH}")),
        Step("logs", (*context, "logs", f"job/{name}", "--tail=-1")),
        Step("drop-collector", (*context, "delete", f"pod/{collector}", *DELETE_FLAGS)),
        Step("apply-collector", (*context, "apply", "-f", str(local / "collector.yaml"))),
        Step(
            "wait-collector",
            (*context, "wait", f"pod/{collector}", "--for=condition=ready", "--timeout=5m"),
        ),
        # WHY exec and tar rather than `kubectl cp`: cp wraps the same tar in its
        # own copy loop, and that loop ends the stream early often enough to cost
        # a cell everything it produced. `k8s-rules-service` lost its film, its
        # attempt and every per-phase log to `error: unexpected EOF` twice in one
        # run, three retries and all. This is the pipe the NAS lane already pulls
        # through, so the bytes go from the remote tar to the local one with
        # nothing in between deciding when they have stopped.
        #
        # WHY `-C /out .`: the collector mounts the cell's own subPath at the path
        # the Job wrote to, so the archive is rooted where the container script
        # tees and nothing has to agree about a working directory. The image's
        # WORKDIR is /app, and a relative source resolved against it was
        # `tar: setup-matrix/<cell>: Cannot stat` on the second real run.
        Step(
            COPY_OUT,
            (*context, "exec", collector, "--", "tar", "-C", REMOTE_OUT, "-cf", "-", "."),
            pipe_to=("tar", "-C", str(local), "-xf", "-"),
        ),
        Step("delete-collector", (*context, "delete", "pod", collector, "--ignore-not-found")),
        Step("delete", (*context, "delete", "job", name, "--ignore-not-found")),
        # The results are on this machine by now, and the next cell's apply makes
        # the claim again. The data claim is deliberately left alone: it carries
        # the models and the annotation bank, warm across cells and across runs.
        Step(
            "delete-output-claim",
            (*context, "delete", "pvc", OUTPUT_CLAIM, "--ignore-not-found"),
        ),
    )


def k8s_diagnostics(cell: Cell) -> tuple[Step, ...]:
    """What to ask the cluster when a cluster cell's step gives up.

    The events are asked for separately, and namespace-wide, because they outlive
    the pod they are about: the pod a `wait` gave up on is routinely gone by the
    time anyone describes it, and the describe then says `Events: <none>` and
    nothing else. `--sort-by` puts the newest last, which is the tail that prints.
    """
    return (
        Step("describe", (*KUBECTL, "describe", "pod", "-l", f"job-name=setup-matrix-{cell.id}")),
        Step("events", (*KUBECTL, "get", "events", "--sort-by=.lastTimestamp")),
    )


def purge_claims_command() -> tuple[str, ...]:
    """Both claims, for a run that asked to leave the cluster with nothing of its own."""
    return (*KUBECTL, "delete", "pvc", DATA_CLAIM, OUTPUT_CLAIM, "--ignore-not-found")


CPU_OVERLAY = "deploy/kubernetes/overlays/inference"
CUDA_OVERLAY = "deploy/kubernetes/overlays/inference-cuda"
LAN_OVERLAY = "deploy/kubernetes/overlays/inference-lan"
LAN_SERVICE = "inference-lan"
# The in-cluster Service, which is what the runner port-forwards to warm up.
INFERENCE_SERVICE = "inference"
# The service's own image. The committed overlays pin a release of their own, and
# a pin ages: the cluster lane last ran 0.85.0-cuda while the cells ran a 0.86.2
# app image, so those rows measured a service two releases behind the code they
# were published as. The matrix renders the overlay and rewrites this reference.
INFERENCE_IMAGE = "ghcr.io/sam-dumont/immich-video-memory-generator/inference"
_INFERENCE_IMAGE_LINE = re.compile(
    rf"(?m)^(\s*image:\s*){re.escape(INFERENCE_IMAGE)}(?::\S+)?[ \t]*$"
)
INFERENCE_PORT = 8092
# The override. Set it to pin the address the NAS cells call; leave it unset and
# the runner reads it off the LoadBalancer the `inference-lan` overlay asks for.
INFERENCE_ENV = "MATRIX_INFERENCE_BASE_URL"
DERIVED_ADDRESS = "<derived at run time>"

# Long enough for a claim to be provisioned and a node to be picked, short
# enough that "nothing can run this" is an answer rather than an afternoon.
SCHEDULING_TIMEOUT = "5m"

# What the runner polls a Job with. Both counters are asked for by name because
# `{.status.succeeded}{.status.failed}` prints "1" for either outcome, and the
# whole point is telling them apart. `setup_matrix_readiness.job_outcome` reads it.
JOB_STATUS_PATH = "succeeded={.status.succeeded} failed={.status.failed}"

KUBECTL = ("kubectl", "--context", "$MATRIX_K8S_CONTEXT", "-n", "$MATRIX_K8S_NAMESPACE")
# `--wait` so the next step's `apply` cannot race the deletion it depends on: a
# Job's pods take a moment to go, and the name is taken until they have.
DELETE_FLAGS = ("--ignore-not-found", "--wait=true")
# What every matrix Job's pods are labelled, so one selector clears them all.
MATRIX_POD_LABEL = "app.kubernetes.io/component=setup-matrix"
# Long enough for a terminating pod to release a ReadWriteOnce claim, short
# enough that a cell fails saying so instead of waiting out the three-hour cap.
DRAIN_TIMEOUT = "--timeout=3m"
# `.ip` on most controllers, `.hostname` on the ones that hand out a name.
LB_ADDRESS_PATH = "{.status.loadBalancer.ingress[0].ip}"

INFERENCE_DEPLOYMENT = "deployment/immich-memories-inference"
# An image pull, which is the slow part of a re-applied tag. The warm-up's own
# budget is for models being loaded, not for the layers arriving.
INFERENCE_ROLLOUT_TIMEOUT = "10m"
# The dry run prints this as `wait-inference` and the runner runs this same
# tuple, so the transcript cannot promise a wait the run does not take.
INFERENCE_ROLLOUT = (
    *KUBECTL,
    "rollout",
    "status",
    INFERENCE_DEPLOYMENT,
    f"--timeout={INFERENCE_ROLLOUT_TIMEOUT}",
)


def overlay_path(device: str) -> str:
    """Which overlay directory a device choice applies. `auto` is resolved before this."""
    if device not in {"cpu", "cuda"}:
        raise PlanError(f"inference device must be cpu or cuda by now, got {device!r}")
    return CPU_OVERLAY if device == "cpu" else CUDA_OVERLAY


def inference_image(tag: str, *, device: str) -> str:
    """The inference image for a device. Only the `-cuda` build carries that provider."""
    if device not in {"cpu", "cuda"}:
        raise PlanError(f"inference device must be cpu or cuda by now, got {device!r}")
    return f"{INFERENCE_IMAGE}:{tag}-cuda" if device == "cuda" else f"{INFERENCE_IMAGE}:{tag}"


def retag_inference(rendered: str, image: str) -> str:
    """Point every inference container in a rendered overlay at `image`.

    The rendered output is rewritten rather than the overlay, because the
    committed files are what a reader applies by hand and the matrix has no
    business editing them to run.
    """
    return _INFERENCE_IMAGE_LINE.sub(lambda match: match.group(1) + image, rendered)


def needs_lan_address(cells: tuple[Cell, ...]) -> bool:
    """Whether anything off the cluster has to call the inference service.

    Only the NAS lane does. A cluster cell reaches it at `http://inference:8092`
    over the cluster's own DNS and needs no address handed out to the LAN.
    """
    return any(cell.lane == "nas" and cell.facts == "service" for cell in cells)


def inference_overlay_steps(*, device: str, keep: bool, lan: bool, tag: str) -> tuple[Step, ...]:
    """Bring the inference service up before the service cells, and take it down after.

    `auto` is resolved at run time by asking the cluster whether any node carries
    the GPU operator's label. A dry run prints the probe instead of its answer,
    because the answer depends on a cluster the transcript should not assume.

    The overlay is rendered and its image reference rewritten to `tag` before it
    is applied, so the service under test is the release the cells are running
    rather than whatever the committed pin last named.

    `lan` adds the second Service that asks for a LoadBalancer address, which is
    the only way a NAS outside the cluster can reach the port. The address is read
    back rather than configured: it is whatever the controller hands out, and it
    is never written to a file in the repo.
    """
    probe = Step(
        "probe-gpu", (*KUBECTL, "get", "nodes", "-l", "nvidia.com/gpu.present=true", "-o", "name")
    )
    auto = device == "auto"
    overlay = "<cpu or cuda, decided by probe-gpu>" if auto else overlay_path(device)
    image = (
        f"{INFERENCE_IMAGE}:{tag}<-cuda if probe-gpu finds one>"
        if auto
        else inference_image(tag, device=device)
    )
    steps = [
        probe if auto else Step("device", ("echo", f"inference device pinned: {device}")),
        Step("inference-image", ("echo", image)),
        Step(
            "apply-inference",
            ("kubectl", "kustomize", overlay),
            pipe_to=(*KUBECTL, "apply", "-f", "-"),
        ),
        Step("wait-inference", INFERENCE_ROLLOUT),
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
    cell: Cell,
    memory: dict,
    month: str,
    image: str,
    config_yaml: str,
    *,
    operator_immich: bool,
    fresh: bool,
) -> dict[str, str]:
    """A ConfigMap with the pinned config, a Job that mounts it, and the collector.

    Built from `deploy/kubernetes/base/job.yaml`: same securityContext, same model
    and output claims. The command is the same container script the NAS runs, so a
    cluster cell reports peak memory the way a NAS cell does.
    """
    name = f"setup-matrix-{cell.id}"
    config_block = "\n".join(f"    {line}" for line in config_yaml.splitlines())
    script = "\n".join(
        f"                {part}"
        for part in _container_script(cell, memory, month, fresh=fresh).split("; ")
    )
    _, credentials = _variables(cell.config)
    secret_block = (
        f"          envFrom:\n            - secretRef:\n                name: {name}-secrets\n"
        if credentials or operator_immich
        else ""
    )
    # The switch the container script reads. Absent unless the run asked for it,
    # so the Job manifest says on its face whether this cell started from nothing.
    fresh_block = (
        f'          env:\n            - name: {FRESH_CACHE_ENV}\n              value: "1"\n'
        if fresh
        else ""
    )
    return {
        "claims.yaml": _CLAIMS.format(data=DATA_CLAIM, output=OUTPUT_CLAIM),
        "configmap.yaml": _CONFIGMAP.format(name=name, config=config_block),
        "job.yaml": _JOB.format(
            name=name,
            image=image,
            script=script,
            config=REMOTE_CONFIG,
            out=REMOTE_OUT,
            cache=REMOTE_CACHE,
            fresh=fresh_block,
            secrets=secret_block,
            subpath=f"{OUTPUT_SUBPATH}/{cell.id}",
            models_subpath=MODELS_SUBPATH,
            cache_subpath=f"{CACHE_SUBPATH}/{cell.id}",
            output_claim=OUTPUT_CLAIM,
            data_claim=DATA_CLAIM,
        ),
        "collector.yaml": _COLLECTOR.format(
            name=f"{name}-collect",
            image=image,
            out=REMOTE_OUT,
            output_claim=OUTPUT_CLAIM,
            subpath=f"{OUTPUT_SUBPATH}/{cell.id}",
        ),
    }


# Applied before every cluster cell, and applying an unchanged claim changes
# nothing, so one cell's run leaves the next one's storage already there.
_CLAIMS = """# The models and the annotation bank, kept between cells and between runs.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {data}
  namespace: $MATRIX_K8S_NAMESPACE
  labels:
    app.kubernetes.io/name: immich-memories
    app.kubernetes.io/component: setup-matrix
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
---
# One cell's output, deleted once the collector has copied it to this machine.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {output}
  namespace: $MATRIX_K8S_NAMESPACE
  labels:
    app.kubernetes.io/name: immich-memories
    app.kubernetes.io/component: setup-matrix
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
"""

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
    metadata:
      labels:
        app.kubernetes.io/name: immich-memories
        app.kubernetes.io/component: setup-matrix
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
{fresh}{secrets}          volumeMounts:
            - name: config
              mountPath: {config}
              subPath: config.yaml
            - name: output
              mountPath: {out}
              subPath: {subpath}
            # One claim, two mounts: the models are downloaded once for the whole
            # matrix, the editorial cache belongs to this cell alone.
            - name: data
              mountPath: /models
              subPath: {models_subpath}
            - name: data
              mountPath: {cache}
              subPath: {cache_subpath}
          # The request is what the scheduler has to find: a 1-CPU app pod was
          # already answered with Insufficient cpu here, and a cell that runs on
          # scraps is not a measurement. The limit is the NAS cell's docker cap,
          # 4 CPU and 4 GB, so the two rows in the table are comparable.
          resources:
            requests:
              memory: "4Gi"
              cpu: "2000m"
            limits:
              memory: "4Gi"
              cpu: "4000m"
      volumes:
        - name: config
          configMap:
            name: {name}-config
        - name: output
          persistentVolumeClaim:
            claimName: {output_claim}
        - name: data
          persistentVolumeClaim:
            claimName: {data_claim}
"""

# Mounts the output claim and does nothing, so `kubectl cp` has a running
# container to shell into after the Job's own pod has finished. Same mountPath
# and same subPath as the Job, so the copy reads the directory the Job wrote.
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
          subPath: {subpath}
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
        claimName: {output_claim}
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
    fresh_cache: bool = False,
) -> CellPlan:
    """One cell's commands, manifests and pins, with no value from `environment` inside."""
    pins = _pins_for(cell, manifest, library)
    source = {**(library.get("config") or {}), **cell.config}
    references, credentials = _variables(source)
    required = tuple(dict.fromkeys((*cell.requires_env, *references, *credentials)))
    missing = [name for name in required if not (environment.get(name) or "").strip()]
    skip = f"needs {', '.join(missing)}, absent from the loaded environment" if missing else None

    cache = str(out_dir / cell.id / CELL_CACHE_DIR) if cell.lane == "mac" else REMOTE_CACHE
    pins.update(cache_pins(cache))
    # The cluster's ConfigMap is built from the pins alone, on purpose: the
    # operator's config never lands in one. For a library that pins no Immich of
    # its own that left the Job with no server to read, and run 2's four k8s
    # cells died together on "Immich not configured". The URL is carried here,
    # because a server address is not a secret; the key is in the cell's Secret.
    operator_immich = cell.lane == "k8s" and not library_pins_immich(library)
    config_pins = {**pins, "immich.url": FROM_OPERATOR_CONFIG} if operator_immich else pins
    config_yaml = yaml.safe_dump(nested(config_pins), sort_keys=False)
    memory = manifest.get("memory") or {}
    manifests: dict[str, str] = {}
    diagnostics: tuple[Step, ...] = ()
    container_limits = ""
    if cell.lane == "mac":
        steps = _mac_steps(cell, memory, month, out_dir, fresh=fresh_cache)
    elif cell.lane == "nas":
        limits = nas_docker_limits(environment)
        steps = _nas_steps(cell, memory, month, out_dir, image, limits, fresh=fresh_cache)
        container_limits = " ".join(limits)
    elif cell.lane == "k8s":
        steps = _k8s_steps(cell, out_dir, operator_immich=operator_immich)
        manifests = _k8s_manifests(
            cell,
            memory,
            month,
            image,
            config_yaml,
            operator_immich=operator_immich,
            fresh=fresh_cache,
        )
        diagnostics = k8s_diagnostics(cell)
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
        operator_immich=operator_immich,
        fresh_cache=fresh_cache,
        container_limits=container_limits,
        skip_reason=skip,
        diagnostics=diagnostics,
    )


def nested(pins: dict[str, Any]) -> dict[str, Any]:
    """Dotted pins as the nested mapping a config file holds.

    A `DROP` names a field to take out of the copied config, so it has nothing to
    write here and the cluster's ConfigMap is left without the key at all, which
    is the same outcome.
    """
    out: dict[str, Any] = {}
    for dotted, value in sorted(pins.items()):
        if value is DROP:
            continue
        *branches, leaf = dotted.split(".")
        target = out
        for branch in branches:
            target = target.setdefault(branch, {})
        target[leaf] = value
    return out


def _check_ssh_destination(environment: dict[str, str]) -> None:
    """The NAS variable is a destination, not a command line.

    It is substituted where ssh expects `[user@]host`, so a value like
    `ssh -i key admin@nas` reaches ssh as a username and the first step dies with
    "remote username contains invalid characters", naming nothing useful.
    Checking it here means `--dry-run` says so too, before anything connects.
    """
    value = (environment.get(NAS_SSH_ENV) or "").strip()
    if value and any(character.isspace() for character in value):
        raise PlanError(
            f"{NAS_SSH_ENV} contains whitespace, so it is being set to a command. It is an ssh "
            "destination: `user@host`, or better a Host alias from ~/.ssh/config that carries the "
            "key, the user, BatchMode and ConnectTimeout."
        )


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
    fresh_cache: bool = False,
) -> Plan:
    """The whole request as a plan, skipped cells included."""
    _check_ssh_destination(environment)
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
                fresh_cache=fresh_cache,
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
        lines += [f"  {step.name:<19} {step}" for step in overlay]
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
        lines += [f"   {step.name:<19} {step}" for step in item.steps]
        lines += [f"   {'on failure':<19} {step}" for step in item.diagnostics]
        for name, body in item.manifests.items():
            lines.append(f"   manifest {name}:")
            lines += [f"     {line}" for line in body.rstrip().splitlines()]
    if plan.skipped:
        lines += ["", "skipped: " + ", ".join(item.cell.id for item in plan.skipped)]
    return "\n".join(lines) + "\n"


def _plain(part: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_@%+=:,./$-]+", part))


def _render(command: tuple[str, ...]) -> str:
    return " ".join(part if _plain(part) else shlex.quote(part) for part in command)
