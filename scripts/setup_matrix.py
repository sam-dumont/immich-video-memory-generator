"""Run one memory through every supported setup, and publish what each one cost.

The capability matrix varies what the product is asked for. This varies the
machine it runs on: host lane x reader x where the picture facts come from x
preparation tier, over the SAME month of the SAME library. The output answers one
question: how do the same pictures come out under each mode, and what does each
mode tax.

Local only. Results land under `output/setup-matrix/`, which is gitignored,
because a real run carries real footage.

    # See the whole plan without touching anything:
    uv run python scripts/setup_matrix.py --dry-run

    # Put the fixture library on the LAN so the NAS and the cluster can read it:
    uv run python scripts/setup_matrix.py --serve-fixture --library demo

    # One lane, for real:
    uv run python scripts/setup_matrix.py --lane mac --library demo
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_pinned_config import pinned_config, read_operator_immich  # noqa: E402
from setup_matrix_capture import (  # noqa: E402
    RunSummary,
    anonymize,
    latest_attempt,
    parse_cache_primed,
    parse_cgroup_cpu_seconds,
    parse_cgroup_peak_rss_mb,
    parse_models_fetch_seconds,
    parse_prepare_seconds,
    parse_prepared_pictures,
    parse_prepared_producers,
    parse_run_summary,
    parse_saved_path,
    parse_time_peak_rss_mb,
    prepare_phases,
    probe_video,
    read_cut,
    read_losses,
)
from setup_matrix_plan import (  # noqa: E402
    CACHE_PRIMED_FILE,
    DERIVED_ADDRESS,
    EDITORIAL_RUNS,
    FIXTURE_ENV,
    FIXTURE_PORT,
    FROM_OPERATOR_CONFIG,
    INFERENCE_DEPLOYMENT,
    INFERENCE_ENV,
    INFERENCE_PORT,
    INFERENCE_ROLLOUT,
    INFERENCE_ROLLOUT_TIMEOUT,
    INFERENCE_SERVICE,
    KUBECTL,
    LAN_OVERLAY,
    LAN_SERVICE,
    MAKE_REMOTE_DIR,
    MODELS_FETCH_SECONDS,
    REMOTE_ATTEMPTS,
    REMOTE_OUT,
    CellPlan,
    Plan,
    PlanError,
    Step,
    build_plan,
    dry_run_text,
    fetches_models,
    inference_image,
    inference_overlay_steps,
    load_manifest,
    needs_lan_address,
    overlay_path,
    purge_claims_command,
    read_cells,
    retag_inference,
)
from setup_matrix_readiness import (  # noqa: E402
    await_facts,
    await_facts_via_forward,
    await_job,
    await_listener,
    await_pod,
    warmup_picture,
)
from setup_matrix_summary import (  # noqa: E402
    build_markdown,
    build_summary,
    read_cell_records,
    write_cell_record,
)

from immich_memories.config_models_inference import SERVED_PRODUCERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILES = (
    Path.home() / ".immich-memories-matrix" / ".env",
    Path.home() / ".immich-memories-matrix" / "matrix.env",
)
# The image that exists on the NAS today. 0.85.0 was never published as an
# image; 0.84.1 is the last published tag and is runtime-identical to main for
# everything this matrix measures.
DEFAULT_IMAGE_TAG = "0.84.1"
IMAGE_REPO = "ghcr.io/sam-dumont/immich-video-memory-generator"
# The pictures the demo library is built from. The inference warm-up sends one of
# them: a real photograph, not a synthetic square, so every model the cells will
# ask for is the one that gets loaded.
FIXTURE_LIBRARY = REPO_ROOT / "tests" / "e2e" / "fixtures" / "library"


def read_env_files(paths: tuple[Path, ...]) -> dict[str, str]:
    """`KEY=value` lines from each file, later files winning, merged over os.environ.

    Values are read but never printed: the runner substitutes them into commands
    only at the moment it executes one, and `--dry-run` never gets that far.
    """
    merged = dict(os.environ)
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            merged[key.strip()] = value.strip().strip("'\"")
    return merged


def lan_address() -> str:
    """This machine's address on the LAN, so a NAS and a cluster can reach the fixture.

    A UDP socket to a public address picks the interface the default route uses
    without sending a packet. `gethostname()` resolves to 127.0.0.1 on macOS,
    which is exactly the failure this avoids.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routed nowhere, never contacted.
        return str(probe.getsockname()[0])


def serve_fixture(root: Path) -> tuple[object, str]:
    """Put the stock June 2024 library on the LAN and return the URL to point cells at.

    Building it renders a thumbnail per picture and takes about fifteen seconds
    before anything is bound, so this says what it is doing first and only comes
    back once the socket actually answers.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from tests.e2e.fake_immich import FakeImmichServer

    print("building the fixture library (one thumbnail per picture, about 15s)...", flush=True)
    server = FakeImmichServer.start(root, host="0.0.0.0", port=FIXTURE_PORT)  # noqa: S104
    await_listener(FIXTURE_PORT)
    return server, f"http://{lan_address()}:{FIXTURE_PORT}"


def _substitute(part: str, environment: dict[str, str]) -> str:
    """Put the environment's values back into a command rendered with `$NAME` references."""
    for name, value in sorted(environment.items(), key=lambda item: -len(item[0])):
        if f"${name}" in part:
            part = part.replace(f"${name}", value)
    return part


# The only per-step peak memory a local cell can get. `getrusage(RUSAGE_CHILDREN)`
# reports the maximum over every child this process has ever reaped, so it hands
# every cell of a lane the same figure: both Mac cells came back at 1014.9 MB.
TIME_BINARY = Path("/usr/bin/time")
_TIME_FLAG = "-l" if sys.platform == "darwin" else "-v"
NO_TIME_REASON = (
    "per-step peak memory. /usr/bin/time is not on this host, and the kernel's own"
    " counter is the maximum over every child the runner ever spawned, which is the"
    " lane rather than the cell."
)
NO_FETCH_REASON = (
    "the pinned models this cell downloaded. The Mac lane runs on an install that"
    " already has them, and a cell taking its picture facts off the service runs"
    " no local model at all."
)


def _run_step(
    step: Step, plan: Plan, item: CellPlan, *, measure: bool = False
) -> subprocess.CompletedProcess:
    resolved = [_substitute(part, plan.environment) for part in step.command]
    if item.operator_immich:
        # `make-secret` is the only step carrying the placeholder, and this is the
        # last moment before the key is in an argv rather than in a plan.
        key = plan.operator_credentials["api_key"]
        resolved = [part.replace(FROM_OPERATOR_CONFIG, key) for part in resolved]
    passthrough = {
        name: plan.environment[name] for name in item.app_credentials if name in plan.environment
    }
    environment = {**os.environ, **passthrough}
    if step.pipe_to:
        sink = [_substitute(part, plan.environment) for part in step.pipe_to]
        return _run_pipe(resolved, sink, environment)
    if measure and TIME_BINARY.is_file():
        resolved = [str(TIME_BINARY), _TIME_FLAG, *resolved]
    return subprocess.run(  # noqa: S603
        resolved,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _run_pipe(
    source_command: list[str], sink_command: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess:
    """Two commands joined by a pipe, run without a shell in between.

    What flows across the pipe is a tar stream, so nothing decodes it on the way
    through; only what the two halves say for the log is decoded. The upstream
    stderr goes to a file rather than a pipe nobody drains while we wait on the
    downstream half, which is how that half would wedge behind a full buffer.
    """
    with tempfile.TemporaryFile() as upstream_errors:
        source = subprocess.Popen(  # noqa: S603
            source_command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=upstream_errors,
            env=environment,
        )
        with source:
            sink = subprocess.Popen(  # noqa: S603
                sink_command,
                cwd=REPO_ROOT,
                stdin=source.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            if source.stdout is not None:
                source.stdout.close()  # the sink holds the read end now
            with sink:
                out, sink_errors = sink.communicate()
            source.wait()
        upstream_errors.seek(0)
        errors = upstream_errors.read() + sink_errors
    return subprocess.CompletedProcess(
        args=[*source_command, "|", *sink_command],
        returncode=sink.returncode or source.returncode,
        stdout=out.decode(errors="replace"),
        stderr=errors.decode(errors="replace"),
    )


def _child_cpu_seconds() -> float:
    """CPU seconds charged to children so far, from the kernel."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run_local_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """The mac lane: three invocations here in this process tree, timed and captured."""
    cell_dir = out_dir / item.cell.id
    cell_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(_substitute(item.cache_dir, plan.environment)).expanduser()
    # A cache of this cell's own, so an existing one means this cell has run
    # before and its `cold` preparation is a re-read of what it banked then.
    record = _new_record(item, primed=cache.is_dir())
    before_cpu = _child_cpu_seconds()
    peaks: list[float] = []

    for step in item.steps:
        started = time.monotonic()
        proc = _run_step(step, plan, item, measure=True)
        elapsed = time.monotonic() - started
        (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
        (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if peak := parse_time_peak_rss_mb(proc.stderr or ""):
            peaks.append(peak)
        if step.name == "prepare-cold":
            record["timing"]["prepare_cold_s"] = parse_prepare_seconds(text) or round(elapsed, 2)
            _apply_prepared(record, text)
        elif step.name == "prepare-warm":
            record["timing"]["prepare_warm_s"] = parse_prepare_seconds(text) or round(elapsed, 2)
        else:
            _apply_run_summary(record, text, cell_dir)
        if proc.returncode != 0:
            record["error"] = f"{step.name} exited {proc.returncode}"
            break

    record["timing"]["peak_rss_mb"] = max(peaks) if peaks else None
    if not peaks:
        record["measurement_notes"]["peak_rss_mb"] = NO_TIME_REASON
    record["timing"]["cpu_s"] = round(_child_cpu_seconds() - before_cpu, 2)
    _apply_attempt(record, cache / EDITORIAL_RUNS, item.cell.id)
    return record


# The two steps that watch the cluster rather than ask it once. A Job that failed
# never satisfies a wait for condition=complete, and a pod that does not exist yet
# is an error to `kubectl wait` rather than something it waits for.
_K8S_POLLS = {"wait-created": await_pod, "wait": await_job}


COPY_OUT = "copy-out"
# `kubectl cp` streams a tar out of the collector, and that stream ends early
# often enough to cost a cell everything it produced: `k8s-rules-service`
# finished its cut and lost its film, its attempt and every per-phase log to one
# `error: unexpected EOF`. The claim is still there and still mounted, so trying
# again is cheap next to re-running the cell.
COPY_OUT_ATTEMPTS = 3
COPY_OUT_PAUSE_S = 5
COPY_OUT_LOST = (
    f"the film. The copy-out failed after {COPY_OUT_ATTEMPTS} attempts, so the file the run"
    " named never reached this machine and nothing here measured its duration, size or codec."
)


def _film_is_missing(record: dict, cell_dir: Path) -> bool:
    """The run named a file it wrote, and that file is not on this machine.

    A zero exit from `kubectl cp` is not proof the copy finished. What the cell
    came for is the film, so that is what the copy is judged on.
    """
    name = STDOUT_OF_THE_RUN.get(record["lane"])
    log = cell_dir / name if name else None
    shown = parse_saved_path(log.read_text()) if log and log.is_file() else None
    return bool(shown) and not any(path.is_file() for path in _video_candidates(shown, cell_dir))


def _copy_out(
    step: Step, plan: Plan, item: CellPlan, record: dict, cell_dir: Path
) -> subprocess.CompletedProcess:
    """The copy, retried until the film it is for is here, and named as lost when it is not."""
    proc = _run_step(step, plan, item)
    for _ in range(COPY_OUT_ATTEMPTS - 1):
        if proc.returncode == 0 and not _film_is_missing(record, cell_dir):
            return proc
        time.sleep(COPY_OUT_PAUSE_S)
        proc = _run_step(step, plan, item)
    if _film_is_missing(record, cell_dir):
        record["measurement_notes"]["film"] = COPY_OUT_LOST
    return proc


def _run_or_poll(
    step: Step, plan: Plan, item: CellPlan, record: dict, cell_dir: Path
) -> subprocess.CompletedProcess:
    """One command, the poll that step stands for, or the copy that has to be checked."""
    if step.name == COPY_OUT and item.cell.lane == "k8s":
        return _copy_out(step, plan, item, record, cell_dir)

    def probe() -> subprocess.CompletedProcess:
        return _run_step(step, plan, item)

    poll = _K8S_POLLS.get(step.name) if item.cell.lane == "k8s" else None
    return poll(probe) if poll else probe()


def run_remote_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """The NAS and cluster lanes: push, run, pull, then read the same artifacts back."""
    cell_dir = out_dir / item.cell.id
    cell_dir.mkdir(parents=True, exist_ok=True)
    record = _new_record(item, primed=None)
    for step in item.steps:
        proc = _run_or_poll(step, plan, item, record, cell_dir)
        (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
        (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")
        if proc.returncode != 0 and step.name not in {"logs", "delete"}:
            record["error"] = f"{step.name} exited {proc.returncode}"
            for diagnostic in item.diagnostics:
                record["error"] += "\n" + _diagnose(diagnostic, item, plan, cell_dir)
            break

    if record["error"]:
        _finish_failed_cell(item, plan, cell_dir)
    _read_remote_artifacts(record, cell_dir)
    _apply_stranded_summary(record, cell_dir)
    _apply_attempt(record, cell_dir / REMOTE_ATTEMPTS, item.cell.id)
    return record


# What a cell still runs after it has failed: what it has to say, and whatever it
# created. A pod left Pending holds the output claim open, and the next cluster
# cell's teardown would then wait on a claim this one will never release, and a
# NAS cell that died before `drop-credentials` would leave its env file and its
# copy of the operator's config on the NAS.
_AFTER_FAILURE = ("logs", "drop-credentials", "delete-collector", "delete", "delete-output-claim")


def _finish_failed_cell(item: CellPlan, plan: Plan, cell_dir: Path) -> None:
    for step in item.steps:
        if step.name in _AFTER_FAILURE:
            proc = _run_step(step, plan, item)
            (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
            (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")


def _diagnose(diagnostic: Step, item: CellPlan, plan: Plan, cell_dir: Path) -> str:
    """What the cluster says about the pod, for a cell that gave up waiting on it.

    A Pending pod's reason is in its events and nowhere else: the Job object says
    only that nothing has completed.
    """
    proc = _run_step(diagnostic, plan, item)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    (cell_dir / f"{diagnostic.name}.log").write_text(text + "\n")
    tail = "\n".join(text.splitlines()[-12:])
    print(tail, file=sys.stderr)
    return tail


def _read_remote_artifacts(record: dict, cell_dir: Path) -> None:
    """The logs and the cgroup counters the container wrote into its own output volume."""
    for name, field in (
        ("prepare-cold.log", "prepare_cold_s"),
        ("prepare-warm.log", "prepare_warm_s"),
    ):
        path = cell_dir / name
        if not path.is_file():
            continue
        text = path.read_text()
        record["timing"][field] = parse_prepare_seconds(text)
        if field == "prepare_cold_s":
            _apply_prepared(record, text)
    fetched = cell_dir / MODELS_FETCH_SECONDS
    if fetched.is_file():
        record["timing"]["models_fetch_s"] = parse_models_fetch_seconds(fetched.read_text())
    generate = cell_dir / "generate.log"
    if generate.is_file():
        _apply_run_summary(record, generate.read_text(), cell_dir)
    peak = cell_dir / "peak-rss-bytes.txt"
    if peak.is_file():
        record["timing"]["peak_rss_mb"] = parse_cgroup_peak_rss_mb(peak.read_text())
    cpu = cell_dir / "cpu.txt"
    if cpu.is_file():
        record["timing"]["cpu_s"] = parse_cgroup_cpu_seconds(cpu.read_text())
    _apply_cache_primed(record, cell_dir)


# In the order they are trusted: the cluster's answer comes back with the run,
# the NAS's is already on this machine when the pull-results fails.
_CACHE_PRIMED_SOURCES = (CACHE_PRIMED_FILE, f"{MAKE_REMOTE_DIR}.stdout.log")


def _apply_cache_primed(record: dict, cell_dir: Path) -> None:
    """Whether this cell's cache already held a run, as the lane reported it.

    A remote cell used to leave this null, so a cold preparation of 0 s over an
    already-warm bank published as a cold preparation of 0 s, with nothing in
    `unmeasured` to say it was a re-read.
    """
    for name in _CACHE_PRIMED_SOURCES:
        path = cell_dir / name
        if not path.is_file():
            continue
        primed = parse_cache_primed(path.read_text())
        if primed is not None:
            record["prepare_cache_primed"] = primed
            return


def _apply_prepared(record: dict, text: str) -> None:
    pictures, per_picture = parse_prepared_pictures(text)
    record["prepared"] = {
        "pictures": pictures,
        "seconds_per_picture": per_picture,
        "producers": parse_prepared_producers(text),
    }


def _new_record(item: CellPlan, *, primed: bool | None) -> dict:
    cell = item.cell
    return {
        "id": cell.id,
        "lane": cell.lane,
        "reader": cell.reader,
        "facts": cell.facts,
        "tier": cell.tier,
        "why": cell.why,
        "hosted": cell.hosted,
        "skip_reason": item.skip_reason,
        "prepare_cache_primed": primed,
        # What the container was actually pinned to, which is not the same on
        # every NAS: a kernel with no CFS controller takes a cpuset, not a quota.
        "container_limits": item.container_limits or None,
        "timing": {
            "models_fetch_s": None,
            "prepare_cold_s": None,
            "prepare_warm_s": None,
            "selection_s": None,
            "render_s": None,
            "total_s": None,
            "peak_rss_mb": None,
            "cpu_s": None,
        },
        # Why a field the run did not report is missing, where "the run did not
        # report it" is not the whole story. Read by the summary's unmeasured list.
        "measurement_notes": {} if fetches_models(cell) else {"models_fetch_s": NO_FETCH_REASON},
        "prepared": {},
        "hosted_usage": {},
        "selected_asset_ids": [],
        "cut": {},
        "losses": {},
        "video": {},
        "error": None,
    }


def _apply_run_summary(record: dict, text: str, cell_dir: Path) -> None:
    summary = parse_run_summary(text)
    _apply_measured_block(record, summary)
    if summary.video_path:
        found = _locate_video(summary.video_path, cell_dir)
        record["video"] = probe_video(found) or {}
        record["video"]["path"] = str(found)


def _apply_measured_block(record: dict, summary: RunSummary) -> None:
    record["timing"]["selection_s"] = summary.selection_s
    record["timing"]["render_s"] = summary.render_s
    record["timing"]["total_s"] = summary.total_s
    record["planned"] = summary.planned
    record["eligible"] = summary.eligible
    record["hosted_usage"] = summary.usage.as_dict()


# Where a remote cell's own stdout landed on this machine, which is not where the
# container put it: the container tees every phase into the output volume, and the
# copy-out is what brings that back. `kubectl logs` and the ssh session return the
# same text without it.
STDOUT_OF_THE_RUN = {"k8s": "logs.stdout.log", "nas": "run.stdout.log"}

STRANDED_ARTIFACTS = (
    "the container wrote it into its output volume and the copy-out never brought it"
    " back. What this row does carry was read off the run's own stdout."
)
STRANDED_FILM = (
    "the film. The run named the file it wrote and the copy-out never brought it back,"
    " so nothing here measured its duration, its size or its codec."
)


def _apply_stranded_preparation(record: dict, text: str) -> None:
    """Both prepare phases out of the run's own stdout, for whatever the files did not carry.

    The container tees cold and warm into files of their own and prints both into
    one stream, and that stream is all there is when the copy-out loses the files.
    The rate table has the same shape in both, so the phases are split before
    either is read: `parse_prepared_producers` over the pair returns one cell's
    producers twice.
    """
    phases = prepare_phases(text)
    for field, phase in zip(("prepare_cold_s", "prepare_warm_s"), phases, strict=False):
        if record["timing"][field] is None:
            record["timing"][field] = parse_prepare_seconds(phase)
    # A truncated `prepare-cold.log` leaves a `prepared` of nulls behind, which is
    # not a count, so the stdout still gets its turn at it.
    if phases and not record["prepared"].get("pictures"):
        _apply_prepared(record, phases[0])


def _apply_stranded_summary(record: dict, cell_dir: Path) -> None:
    """A cell that cut but never got its files back still reports what it printed.

    `k8s-rules-service` published an empty row with `selection 1s, 14 planned from
    130 candidates` and `generation 5m 42s` sitting in the log beside it, because
    the only copy of the end-of-run block the capture read was the one on the
    volume. This reads the copy that came back, and names what did not.
    """
    name = STDOUT_OF_THE_RUN.get(record["lane"])
    log = cell_dir / name if name else None
    if log is None or not log.is_file():
        return
    text = log.read_text()
    _apply_stranded_preparation(record, text)
    summary = parse_run_summary(text)
    if record["timing"]["total_s"] is not None or summary.total_s is None:
        return
    _apply_measured_block(record, summary)
    for field, value in record["timing"].items():
        if value is None:
            record["measurement_notes"].setdefault(field, STRANDED_ARTIFACTS)
    if summary.video_path and _film_is_missing(record, cell_dir):
        record["measurement_notes"].setdefault("film", STRANDED_FILM)


def _video_candidates(shown: str, cell_dir: Path) -> list[Path]:
    """Every path the file a run named could be at on this machine, best first.

    A local cell prints whatever `--output` was given relative to the directory
    the runner starts it in, and a remote cell prints a path inside its own
    container, which `pull-results` and `copy-out` mirror into the cell directory.
    """
    named = Path(shown)
    candidates = [named if named.is_absolute() else REPO_ROOT / named]
    if shown.startswith(f"{REMOTE_OUT}/"):
        candidates.append(cell_dir / shown[len(REMOTE_OUT) + 1 :])
    candidates.append(cell_dir / named.name)
    return candidates


def _locate_video(shown: str, cell_dir: Path) -> Path:
    candidates = _video_candidates(shown, cell_dir)
    return next((path for path in candidates if path.is_file()), candidates[0])


def _apply_attempt(record: dict, runs_dir: Path, memory_key: str) -> None:
    attempt = latest_attempt(runs_dir, memory_key)
    if attempt is None:
        return
    cut = read_cut(attempt)
    record["cut"] = cut
    record["selected_asset_ids"] = [shot["asset_id"] for shot in cut["selected"]]
    record["losses"] = read_losses(attempt)


def _resolve_device(device: str, plan: Plan) -> str:
    """Ask the cluster whether a GPU node exists, once, before the k8s lane starts."""
    if device != "auto":
        return device
    proc = subprocess.run(  # noqa: S603
        [
            _substitute(part, plan.environment)
            for part in (
                "kubectl",
                "--context",
                "$MATRIX_K8S_CONTEXT",
                "get",
                "nodes",
                "-l",
                "nvidia.com/gpu.present=true",
                "-o",
                "name",
            )
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return "cuda" if proc.returncode == 0 and proc.stdout.strip() else "cpu"


# A LoadBalancer address is handed out by a controller, not by us, and it can
# take a moment. Bounded so a cluster without a controller fails with a sentence
# instead of hanging until someone notices.
_LAN_ADDRESS_TIMEOUT_S = 180
_LAN_ADDRESS_POLL_S = 3


def _lan_address(plan: Plan) -> str:
    """The address the `inference-lan` Service was given, polled until it exists.

    Read back rather than configured: it is whatever the cluster's controller
    hands out, so no address is written down anywhere in the repo. `.ip` on most
    controllers, `.hostname` on the ones that answer with a name.
    """
    deadline = time.monotonic() + _LAN_ADDRESS_TIMEOUT_S
    paths = ("{.status.loadBalancer.ingress[0].ip}", "{.status.loadBalancer.ingress[0].hostname}")
    while True:
        for path in paths:
            command = (*KUBECTL, "get", "service", LAN_SERVICE, "-o", f"jsonpath={path}")
            proc = subprocess.run(  # noqa: S603
                [_substitute(part, plan.environment) for part in command],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"service/{LAN_SERVICE} still has no LoadBalancer address after "
                f"{_LAN_ADDRESS_TIMEOUT_S}s. The cluster needs a load-balancer controller for "
                f"the NAS cells to reach the inference service, or set {INFERENCE_ENV} to an "
                "address that already works."
            )
        time.sleep(_LAN_ADDRESS_POLL_S)


def warm_inference(plan: Plan) -> float:
    """One real facts request before any cell makes one, and how long it took to answer.

    A rolled-out Deployment is not a service that can decide a picture: the models
    land in its cache on the first request that wants them, and until they do every
    call is a 503. Without this the first service cell measured that download as its
    own preparation time, which is not what the row claims to be.
    """
    pictures = sorted(FIXTURE_LIBRARY.glob("*.jpg"))
    if not pictures:
        raise SystemExit(f"no picture under {FIXTURE_LIBRARY} to warm the service with")
    image = warmup_picture(pictures[0])
    address = plan.environment.get(INFERENCE_ENV) or ""
    producers = tuple(SERVED_PRODUCERS)
    # An address the NAS cells will use is an address this host can use too, and
    # it needs no second process that can fail on its own.
    if address and address != DERIVED_ADDRESS:
        return await_facts(address, image, producers)
    kubectl = tuple(_substitute(part, plan.environment) for part in KUBECTL)
    return await_facts_via_forward(kubectl, INFERENCE_SERVICE, INFERENCE_PORT, image, producers)


def _write_cell_config(item: CellPlan, plan: Plan, out_dir: Path, config: Path | None) -> None:
    pins = {
        key: _substitute(value, plan.environment) if isinstance(value, str) else value
        for key, value in item.pins.items()
    }
    pinned_config(config, out_dir / item.cell.id / "config.yaml", pins)
    for name, body in item.manifests.items():
        rendered = _substitute(body, plan.environment)
        if item.operator_immich:
            rendered = rendered.replace(FROM_OPERATOR_CONFIG, plan.operator_credentials["url"])
        (out_dir / item.cell.id / name).write_text(rendered)


def run_lane(lane: str, items: list[CellPlan], plan: Plan, out_dir: Path) -> list[dict]:
    # One heavy job at a time, because a lane is one host and every number this
    # matrix publishes is a timing taken on it. The Mac kernel-panicked under
    # parallel load and is the only lane allowed to call the local model server.
    # The NAS pins each cell to `--cpus 4 --memory 4g` and has exactly 4 cores,
    # so two cells there measure contention, not the setup. The cluster cells
    # share one inference service and can land on the same node, which does the
    # same thing. Lanes still run against each other: different hosts contend
    # for nothing.
    runner = run_local_cell if lane == "mac" else run_remote_cell
    return [runner(item, plan, out_dir) for item in items]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--lane", action="append", choices=["mac", "nas", "k8s", "all"], default=[])
    parser.add_argument("--cell", action="append", default=[], help="cell id, repeatable")
    parser.add_argument(
        "--library", default="demo", help="demo (fixture) or a name from the manifest"
    )
    parser.add_argument("--month", default=None, help="YYYY-MM; defaults to the library's month")
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, change nothing"
    )
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None, help="config to copy for credentials")
    parser.add_argument(
        "--anonymize", action="store_true", help="strip ids and text from the summary"
    )
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="rebuild the summary from the cells already under --out, and run nothing",
    )
    parser.add_argument(
        "--serve-fixture", action="store_true", help="serve the fixture library on the LAN"
    )
    parser.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    parser.add_argument(
        "--inference-tag",
        default=None,
        help="image tag for the inference service; defaults to --image-tag",
    )
    parser.add_argument("--inference-device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--keep-service", action="store_true", help="leave the inference overlay running"
    )
    parser.add_argument(
        "--purge-claims",
        action="store_true",
        help="delete the cluster claims at the end, models and annotation bank included",
    )
    return parser.parse_args(argv)


def _lanes(requested: list[str]) -> tuple[str, ...]:
    return () if not requested or "all" in requested else tuple(dict.fromkeys(requested))


def _chosen_cells(manifest: dict, opts: argparse.Namespace) -> tuple:
    """The cells this request covers, before a plan exists.

    Needed early: whether the LoadBalancer address has to be derived decides what
    goes into the environment the plan is built from.
    """
    lanes, ids = _lanes(opts.lane), tuple(opts.cell)
    return tuple(
        cell
        for cell in read_cells(manifest)
        if (not lanes or cell.lane in lanes) and (not ids or cell.id in ids)
    )


def main(argv: list[str] | None = None) -> int:
    opts = _parse_args(argv)
    environment = read_env_files(tuple(opts.env_file) or DEFAULT_ENV_FILES)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = opts.out or REPO_ROOT / "output" / "setup-matrix" / opts.library / stamp

    server = None
    if opts.serve_fixture and opts.dry_run:
        # The address is this machine's, and a dry run's output is meant to be
        # pasted somewhere public, so the plan says where it comes from instead.
        environment[FIXTURE_ENV] = "decided at run time by --serve-fixture"
    elif opts.serve_fixture:
        server, url = serve_fixture(out_dir / "fixture")
        environment[FIXTURE_ENV] = url
        print(f"fixture library serving at {url} (api key: fake-immich-api-key)")

    manifest = load_manifest()
    chosen = _chosen_cells(manifest, opts)
    lan = needs_lan_address(chosen)
    if lan and not (environment.get(INFERENCE_ENV) or "").strip():
        # A placeholder, so the cell is runnable rather than skipped for a
        # variable nobody is meant to set. `_execute` replaces it with the
        # address the LoadBalancer hands out, before any config is written.
        environment[INFERENCE_ENV] = DERIVED_ADDRESS

    try:
        plan = build_plan(
            manifest=manifest,
            library=opts.library,
            month=opts.month,
            lanes=_lanes(opts.lane),
            cell_ids=tuple(opts.cell),
            out_dir=out_dir,
            image=f"{IMAGE_REPO}:{opts.image_tag}",
            environment=environment,
        )
    except PlanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    needs_overlay = lan or any(item.cell.inference_overlay for item in plan.runnable)
    overlay = (
        inference_overlay_steps(
            device=opts.inference_device,
            keep=opts.keep_service,
            lan=lan,
            tag=opts.inference_tag or opts.image_tag,
        )
        if needs_overlay
        else ()
    )

    if opts.dry_run:
        print(dry_run_text(plan, overlay=overlay))
        _report_skips(plan)
        return 0

    if plan.anonymize_required and not opts.anonymize:
        print(
            f"error: library {plan.library!r} is private. Pass --anonymize before publishing it.",
            file=sys.stderr,
        )
        return 2

    if opts.summarize_only:
        return _summarize(plan, opts, out_dir)

    try:
        return _execute(plan, opts, out_dir, overlay)
    finally:
        if server is not None:
            server.close()


def _execute(plan: Plan, opts: argparse.Namespace, out_dir: Path, overlay: tuple) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    # The service comes up before any config is written, because a NAS cell's
    # config has to carry the address the LoadBalancer hands out and there is no
    # way to know it until the Service exists.
    device = _resolve_device(opts.inference_device, plan) if overlay else "cpu"
    lan = plan.environment.get(INFERENCE_ENV) == DERIVED_ADDRESS
    served = inference_image(opts.inference_tag or opts.image_tag, device=device) if overlay else ""
    warmup: float | None = None
    if overlay:
        print(f"inference overlay: {overlay_path(device)} running {served}")
        bring_up_inference(plan, overlay_path(device), served)
        if lan:
            _apply_overlay(plan, LAN_OVERLAY, up=True)
            plan.environment[INFERENCE_ENV] = f"http://{_lan_address(plan)}:{INFERENCE_PORT}"
            print(f"inference reachable off-cluster on port {INFERENCE_PORT}")
    # Every lane that reads its picture facts from the service waits on this one
    # request, wherever the service came from.
    if any(item.cell.facts == "service" for item in plan.runnable):
        warmup = warm_inference(plan)
        print(f"inference answered a facts request after {warmup:.0f}s")

    if any(item.operator_immich for item in plan.runnable):
        url, api_key = read_operator_immich(opts.config)
        plan.operator_credentials.update({"url": url, "api_key": api_key})

    for item in plan.runnable:
        _write_cell_config(item, plan, out_dir, opts.config)

    records: list[dict] = [
        {**_new_record(item, primed=None), "skip_reason": item.skip_reason} for item in plan.skipped
    ]
    by_lane: dict[str, list[CellPlan]] = {}
    for item in plan.runnable:
        by_lane.setdefault(item.cell.lane, []).append(item)

    # Lanes run together; inside a lane, cells do not.
    with ThreadPoolExecutor(max_workers=max(len(by_lane), 1)) as pool:
        futures = [
            pool.submit(run_lane, lane, items, plan, out_dir) for lane, items in by_lane.items()
        ]
        for future in futures:
            records.extend(future.result())

    # Which service answered this cell, so a row can say what it measured against.
    for row in records:
        if served and row.get("facts") == "service":
            row["inference_image"] = served

    if overlay and not opts.keep_service:
        if lan:
            _apply_overlay(plan, LAN_OVERLAY, up=False)
        _apply_overlay(plan, overlay_path(device), up=False)
    if opts.purge_claims and any(item.cell.lane == "k8s" for item in plan.runnable):
        _run_bare(purge_claims_command(), plan)

    _publish_summary(plan, opts, out_dir, _collect_rows(out_dir, records), warmup)
    _report_skips(plan)
    return 0 if all(row.get("error") is None for row in records) else 1


def _collect_rows(out_dir: Path, fresh: list[dict]) -> list[dict]:
    """This invocation's cells, merged with every cell an earlier one left behind.

    One lane is one invocation into the same `--out`, so a table built from this
    invocation alone would drop every lane that has already run. A cell record on
    disk wins over a skip here: not asking for a lane this time does not unmeasure it.
    """
    for row in fresh:
        if not row.get("skip_reason"):
            write_cell_record(out_dir, row)
    rows = {row["id"]: row for row in fresh if row.get("skip_reason")}
    rows.update({row["id"]: row for row in read_cell_records(out_dir)})
    order = [cell.id for cell in read_cells(load_manifest())]
    return sorted(rows.values(), key=lambda row: _manifest_rank(order, row["id"]))


def _manifest_rank(order: list[str], cell_id: str) -> int:
    return order.index(cell_id) if cell_id in order else len(order)


def _publish_summary(
    plan: Plan,
    opts: argparse.Namespace,
    out_dir: Path,
    rows: list[dict],
    warmup: float | None = None,
) -> None:
    summary = build_summary(
        library=plan.library,
        month=plan.month,
        image=plan.image,
        rows=rows,
        inference_warmup_s=warmup,
    )
    if opts.anonymize:
        summary = anonymize(summary)
    (out_dir / "summary.data.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "summary.md").write_text(build_markdown(summary))
    print(f"\n{out_dir / 'summary.md'}")


def _summarize(plan: Plan, opts: argparse.Namespace, out_dir: Path) -> int:
    """Rebuild the two published files from the cells already on disk, running nothing."""
    rows = _collect_rows(out_dir, [])
    if not rows:
        print(f"error: no cell has run under {out_dir}", file=sys.stderr)
        return 2
    _publish_summary(plan, opts, out_dir, rows)
    return 0


def _run_bare(command: tuple[str, ...], plan: Plan) -> subprocess.CompletedProcess:
    """A command that belongs to the run rather than to a cell, so no cell logs it."""
    return subprocess.run(  # noqa: S603
        [_substitute(part, plan.environment) for part in command],
        cwd=REPO_ROOT,
        check=False,
    )


def bring_up_inference(plan: Plan, path: str, image: str) -> None:
    """Apply the inference overlay and come back only once its new pod can be reached.

    `apply` returns as soon as the API server has the manifest. When the tag has
    changed, the Deployment is pulling by then, and a Service with no ready
    endpoint answers nothing at all: a `port-forward` to one never even gets a
    local listener. That is not a slow service, and the warm-up's fifteen minutes
    are not for it, so the pull is waited out here where `rollout status` is the
    thing that says what happened.
    """
    _apply_overlay(plan, path, up=True, image=image)
    if _run_bare(INFERENCE_ROLLOUT, plan).returncode != 0:
        raise SystemExit(
            f"{INFERENCE_DEPLOYMENT} never rolled out within {INFERENCE_ROLLOUT_TIMEOUT}, so "
            "nothing can reach the inference service and no cell would have measured it."
        )


def _apply_overlay(plan: Plan, path: str, *, up: bool, image: str = "") -> None:
    if not up:
        _run_bare((*KUBECTL, "delete", "-k", path, "--ignore-not-found"), plan)
    elif image:
        _apply_rendered(plan, retag_inference(_render_overlay(path), image))
    else:
        _run_bare((*KUBECTL, "apply", "-k", path), plan)


def _render_overlay(path: str) -> str:
    proc = subprocess.run(  # noqa: S603
        ["kubectl", "kustomize", path],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"kubectl kustomize {path} failed: {proc.stderr.strip()}")
    return proc.stdout


def _apply_rendered(plan: Plan, manifests: str) -> None:
    """Apply what is in hand, because the image the matrix wants is not the committed pin."""
    subprocess.run(  # noqa: S603
        [_substitute(part, plan.environment) for part in (*KUBECTL, "apply", "-f", "-")],
        cwd=REPO_ROOT,
        input=manifests,
        text=True,
        check=False,
    )


def _report_skips(plan: Plan) -> None:
    for item in plan.skipped:
        print(f"skipped {item.cell.id}: {item.skip_reason}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
