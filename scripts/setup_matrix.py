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
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_pinned_config import pinned_config, read_operator_immich  # noqa: E402
from setup_matrix_capture import (  # noqa: E402
    CUT_FROM_LOG,
    RunSummary,
    anonymize,
    apply_exact_usage,
    downloaded_asset_ids,
    film_clip_count,
    latest_attempt,
    parse_cache_primed,
    parse_caption_origins,
    parse_cgroup_cpu_seconds,
    parse_cgroup_peak_rss_mb,
    parse_encoder,
    parse_models_fetch_seconds,
    parse_prepare_seconds,
    parse_prepared_pictures,
    parse_prepared_producers,
    parse_run_summary,
    parse_saved_path,
    parse_time_peak_rss_mb,
    parse_title_backend,
    prepare_phases,
    probe_video,
    read_contract_health,
    read_cut,
    read_images_sent,
    read_losses,
)
from setup_matrix_plan import (  # noqa: E402
    CACHE_PRIMED_FILE,
    CAPTIONER_DEPLOYMENT,
    CAPTIONER_OVERLAYS,
    CAPTIONER_PORT,
    CAPTIONER_ROLLOUT,
    CAPTIONER_ROLLOUT_TIMEOUT,
    CAPTIONER_SERVICE,
    COPY_OUT,
    DERIVED_ADDRESS,
    EDITORIAL_RUNS,
    FIXTURE_ENV,
    FIXTURE_PORT,
    FROM_OPERATOR_CONFIG,
    HOMEBASE_PINS,
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
    check_homebase,
    declared_for_device,
    declared_overlay_steps,
    dry_run_text,
    expand_cells,
    fetches_models,
    inference_image,
    inference_node_command,
    inference_overlay_steps,
    job_requests,
    load_manifest,
    needs_lan_address,
    node_product_command,
    overlay_path,
    pin_inference_node,
    purge_claims_command,
    read_cells,
    required_overlays,
    retag_inference,
)
from setup_matrix_readiness import (  # noqa: E402
    await_captions_via_forward,
    await_facts,
    await_facts_via_forward,
    await_job,
    await_listener,
    await_pod,
    warmup_picture,
)
from setup_matrix_summary import (  # noqa: E402
    CELL_RECORD,
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
# Use the same published release as the shipped Kubernetes deployment.
DEFAULT_IMAGE_TAG = str(
    yaml.safe_load((REPO_ROOT / "deploy/kubernetes/base/kustomization.yaml").read_text())["images"][
        0
    ]["newTag"]
)
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


def _seeded_reasons(source: str) -> dict[str, str]:
    """Why a seeded cell has no preparation number, and where the one it would have is.

    Preparation depends on the host, the tier and where the picture facts come
    from, none of which this cell changed. Running it again would publish one
    measurement under every name that copied it.
    """
    reason = (
        f"preparation. This cell's bank was seeded from `{source}`, which measures it"
        " for this host, this tier and this facts source. Only the reader differs."
    )
    return {"prepare_cold_s": reason, "prepare_warm_s": reason}


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
    # before and its `cold` preparation is a re-read of what it banked then. A
    # run that asked for a fresh cache is cold by construction: the first step
    # takes that directory away before `prepare` is called.
    record = _new_record(
        item,
        primed=False if item.fresh_cache else cache.is_dir(),
        model=_reader_model(item, plan),
    )
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
        elif step.name == "generate":
            _apply_run_summary(record, text, cell_dir)
        if proc.returncode != 0:
            record["error"] = f"{step.name} exited {proc.returncode}"
            break

    record["timing"]["peak_rss_mb"] = max(peaks) if peaks else None
    if not peaks:
        record["measurement_notes"]["peak_rss_mb"] = NO_TIME_REASON
    record["timing"]["cpu_s"] = round(_child_cpu_seconds() - before_cpu, 2)
    _apply_attempt(record, cache / EDITORIAL_RUNS, item.cell.id, cell_dir)
    _apply_stranded_cut(record, cell_dir)
    return record


# The two steps that watch the cluster rather than ask it once. A Job that failed
# never satisfies a wait for condition=complete, and a pod that does not exist yet
# is an error to `kubectl wait` rather than something it waits for.
_K8S_POLLS = {"wait-created": await_pod, "wait": await_job}


# The copy-out streams a tar out of the collector, and a stream ends early often
# enough to cost a cell everything it produced: `k8s-rules-service` finished its
# cut and lost its film, its attempt and every per-phase log to `error:
# unexpected EOF`. The claim is still there and still mounted, so trying again is
# cheap next to re-running the cell.
COPY_OUT_ATTEMPTS = 3
COPY_OUT_PAUSE_S = 5
COPY_OUT_LOST = (
    f"the film. The copy-out failed after {COPY_OUT_ATTEMPTS} attempts, so the file the run"
    " named never reached this machine and nothing here measured its duration, size or codec."
)
# A stream that broke on ONE member is not the same failure. tar exits non-zero
# and stops where the break was, so the film can be here and the attempt behind
# it gone. One more try for what it skipped, and then the row is kept: on
# `k8s-gpu-t1000` a truncated `mastered_*.wav` published `error: copy-out exited
# 1` over a 54.5 s film already sitting in the cell directory.
TRUNCATED_ATTEMPTS = 2
_TRUNCATED_MARKERS = ("Truncated tar archive", "unexpected EOF", "Error exit delayed")
COPY_OUT_TRUNCATED = (
    "the copy-out. The archive broke on a member tar could not finish, so anything"
    " behind it on the stream is still on the volume. The film came back; what tar said was:"
)


def _truncated(proc: subprocess.CompletedProcess) -> bool:
    return any(marker in (proc.stderr or "") for marker in _TRUNCATED_MARKERS)


def _film_is_missing(record: dict, cell_dir: Path) -> bool:
    """The run named a file it wrote, and that file is not on this machine.

    A zero exit from `kubectl cp` is not proof the copy finished. What the cell
    came for is the film, so that is what the copy is judged on.
    """
    name = STDOUT_OF_THE_RUN.get(record["lane"])
    log = cell_dir / name if name else None
    shown = parse_saved_path(log.read_text()) if log and log.is_file() else None
    return bool(shown) and not any(path.is_file() for path in _video_candidates(shown, cell_dir))


def _copy_settled(
    proc: subprocess.CompletedProcess, record: dict, cell_dir: Path, tries: int
) -> bool:
    """Whether there is anything left to gain from copying again."""
    if _film_is_missing(record, cell_dir):
        return False
    if proc.returncode == 0:
        return True
    return _truncated(proc) and tries >= TRUNCATED_ATTEMPTS


def _copy_out(
    step: Step, plan: Plan, item: CellPlan, record: dict, cell_dir: Path
) -> subprocess.CompletedProcess:
    """The copy, retried until the film it is for is here, and named as lost when it is not."""
    proc = _run_step(step, plan, item)
    tries = 1
    while tries < COPY_OUT_ATTEMPTS and not _copy_settled(proc, record, cell_dir, tries):
        time.sleep(COPY_OUT_PAUSE_S)
        proc = _run_step(step, plan, item)
        tries += 1
    if _film_is_missing(record, cell_dir):
        record["measurement_notes"]["film"] = COPY_OUT_LOST
    elif proc.returncode != 0:
        said = ((proc.stderr or "").strip().splitlines() or [f"exit {proc.returncode}"])[0]
        record["measurement_notes"]["copy_out"] = f"{COPY_OUT_TRUNCATED} {said}"
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


# Steps whose non-zero exit never costs the row: `logs` is best-effort and
# `delete` is a tear-down over things that may already be gone.
_LENIENT_STEPS = frozenset({"logs", "delete"})


def _tolerated(step: Step, item: CellPlan, record: dict, cell_dir: Path) -> bool:
    """Whether this step's non-zero exit is allowed to leave the row standing.

    A copy whose archive broke on one member but still brought the film is a note
    on the row, not the loss of it. `k8s-gpu-t1000` published `selected_asset_ids:
    []` and `error: copy-out exited 1` with its 54.5 s film in the directory.
    """
    if step.name in _LENIENT_STEPS:
        return True
    if step.name != COPY_OUT or item.cell.lane != "k8s":
        return False
    return not _film_is_missing(record, cell_dir)


def run_remote_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """The NAS and cluster lanes: push, run, pull, then read the same artifacts back."""
    cell_dir = out_dir / item.cell.id
    cell_dir.mkdir(parents=True, exist_ok=True)
    record = _new_record(item, primed=None, model=_reader_model(item, plan))
    for step in item.steps:
        proc = _run_or_poll(step, plan, item, record, cell_dir)
        (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
        (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")
        if proc.returncode != 0 and not _tolerated(step, item, record, cell_dir):
            record["error"] = f"{step.name} exited {proc.returncode}"
            for diagnostic in item.diagnostics:
                record["error"] += "\n" + _diagnose(diagnostic, item, plan, cell_dir)
            break

    if record["error"]:
        _finish_failed_cell(item, plan, cell_dir)
    _read_remote_artifacts(record, cell_dir)
    _apply_stranded_summary(record, cell_dir)
    _apply_attempt(record, cell_dir / REMOTE_ATTEMPTS, item.cell.id, cell_dir)
    _apply_stranded_cut(record, cell_dir)
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


# Where each lane leaves the two preparation phases. A remote container tees them
# into its output volume under their own names; the Mac lane is two invocations
# the runner captured itself.
_REMOTE_PHASE_LOGS = (
    ("prepare-cold.log", "prepare_cold_s"),
    ("prepare-warm.log", "prepare_warm_s"),
)
_LOCAL_PHASE_LOGS = (
    ("prepare-cold.stdout.log", "prepare_cold_s"),
    ("prepare-warm.stdout.log", "prepare_warm_s"),
)


def _apply_phase_logs(record: dict, cell_dir: Path, names: tuple[tuple[str, str], ...]) -> None:
    for name, field in names:
        path = cell_dir / name
        if not path.is_file():
            continue
        text = path.read_text()
        record["timing"][field] = parse_prepare_seconds(text)
        if field == "prepare_cold_s":
            _apply_prepared(record, text)


def _read_remote_artifacts(record: dict, cell_dir: Path) -> None:
    """The logs and the cgroup counters the container wrote into its own output volume."""
    _apply_phase_logs(record, cell_dir, _REMOTE_PHASE_LOGS)
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
        "caption_origins": parse_caption_origins(text),
    }


def _new_record(item: CellPlan, *, primed: bool | None, model: str | None = None) -> dict:
    cell = item.cell
    return {
        "id": cell.id,
        "lane": cell.lane,
        "reader": cell.reader,
        "facts": cell.facts,
        "tier": cell.tier,
        "why": cell.why,
        "hosted": cell.hosted,
        # Which model this cell's reader actually used, with any `$env:` reference
        # resolved. The price table is keyed by it, and five Mac cells differ in
        # nothing else, so a row with no model id is a row nothing can price or
        # tell apart.
        "reader_model": model,
        # The card this cell rendered on. It is the label the Job selects on, so
        # the pod could not have run anywhere else: a node without it does not
        # match, and the cell would have failed Pending rather than moved.
        "gpu_product": cell.gpu_product or None,
        # What actually drew the titles and what actually encoded the film, read
        # off the run's own log on every lane. The two are not one answer: the
        # first cluster Jobs drew their titles on CUDA and still encoded in
        # software, because the runtime gave the pod `compute,utility` and NVENC
        # was never there to probe.
        "title_backend": None,
        "encoder": None,
        "skip_reason": item.skip_reason,
        # The cell this one's bank came from, and what that makes of the
        # "was the cache already warm" field: a seeded cell is neither cold nor
        # a re-read of its own last run, so it says which it is in words.
        "seeded_from": cell.seed_cache_from or None,
        "prepare_cache_primed": f"seeded from {cell.seed_cache_from}"
        if cell.seed_cache_from
        else primed,
        # Whether the run emptied this cell's bank before it prepared, which is
        # the difference between a cold number and a replay of the last run's.
        "fresh_cache": item.fresh_cache,
        # What the container was actually pinned to, which is not the same on
        # every NAS: a kernel with no CFS controller takes a cpuset, not a quota.
        "container_limits": item.container_limits or None,
        # Whether this cell was handed a home to plan against, and never which
        # one: coordinates are the operator's and belong in no published file.
        # It is the field that lets the table say the lanes measured the same
        # happenings against the same place, which run 1's did not.
        "homebase": "pinned" if all(pin in item.pins for pin in HOMEBASE_PINS) else "unset",
        # What a cluster cell's Job asked the scheduler for. Not always the
        # default: a cell whose work is on a card asks for less so it can be
        # scheduled beside whatever else that node is already running.
        "job_requests": dict(zip(("cpu", "memory"), job_requests(cell), strict=True))
        if cell.lane == "k8s"
        else None,
        # Whether the Job asked the device plugin for a card, which is not the
        # same question as whether it rendered on one: the node's own runtime
        # exposes the card to every pod on it.
        "gpu_resource_requested": bool(cell.gpu_product and cell.gpu_resource)
        if cell.lane == "k8s"
        else None,
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
        "measurement_notes": ({} if fetches_models(cell) else {"models_fetch_s": NO_FETCH_REASON})
        | (_seeded_reasons(cell.seed_cache_from) if cell.seed_cache_from else {}),
        "prepared": {},
        "hosted_usage": {},
        # How often a reading contract refused this cell's reader, and how often
        # the run asked again. Overlap says which pictures a reader chose; this
        # says what it took to get an answer in the shape the contract asked for.
        "contract": {"rejections": None, "repairs": None},
        "selected_asset_ids": [],
        # Where the ids above came from. None means the editor's own record of
        # the cut; anything else names the second-best source it fell back to.
        "cut_source": None,
        "cut": {},
        "losses": {},
        "video": {},
        "error": None,
    }


def _reader_model(item: CellPlan, plan: Plan) -> str | None:
    """The model id a cell's reader will use, with a `$env:` reference resolved.

    None for the cells that pin none: `mac-local` deliberately reads with whatever
    the operator's own config names resident, and a rules cell reads with nothing.
    """
    pinned = item.pins.get("llm.model")
    return _substitute(pinned, plan.environment) if isinstance(pinned, str) else None


def _apply_render_device(record: dict, text: str) -> None:
    """What drew the titles and what encoded the film, from lines the run printed.

    Never inferred from the lane. A cell that printed neither keeps None and the
    table shows a dash: "it is a CPU cell, so it must have been libx264" is a
    guess, and this table does not publish those.
    """
    record["title_backend"] = record.get("title_backend") or parse_title_backend(text)
    record["encoder"] = record.get("encoder") or parse_encoder(text)


def _apply_run_summary(record: dict, text: str, cell_dir: Path) -> None:
    _apply_render_device(record, text)
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
    _apply_render_device(record, text)
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


# Wherever a lane left what `generate` printed. The mac lane splits it in two
# because it captured the streams separately; a remote one has the volume's copy
# and the session's copy of the same text. Reading every one of them is safe: the
# episode reader warns once per distinct reason and the count deduplicates on it.
_GENERATE_OUTPUT = (
    "generate.log",
    "generate.stdout.log",
    "generate.stderr.log",
    *STDOUT_OF_THE_RUN.values(),
)


def _generate_output(cell_dir: Path) -> str:
    paths = (cell_dir / name for name in _GENERATE_OUTPUT)
    return "\n".join(path.read_text() for path in paths if path.is_file())


STRANDED_CUT = (
    "the cut's reasons. The attempt directory stayed on the volume, so the {count} pictures"
    " below are the ones the run downloaded rather than the editor's own record of them,"
    " and the order they play in is not among the things a download line says."
)
CUT_DISAGREES = (
    " The film was assembled from {clips} clips, which is not {count}, so even the count is"
    " the log's and not the cut's."
)


def _apply_stranded_cut(record: dict, cell_dir: Path) -> None:
    """Which pictures a cell kept, for one whose attempt never reached this machine.

    `k8s-gpu-t1000` published `selected_asset_ids: []` and `#kept 0` beside a
    54.5 s film, because a truncated tar left the attempt on the volume. The run
    had named every picture it fetched in its own log, and the film had already
    counted its own clips.
    """
    if record["selected_asset_ids"]:
        return
    text = _generate_output(cell_dir)
    ids = downloaded_asset_ids(text)
    if not ids:
        return
    record["selected_asset_ids"] = ids
    record["cut_source"] = CUT_FROM_LOG
    note = STRANDED_CUT.format(count=len(ids))
    clips = film_clip_count(text)
    if clips is not None and clips != len(ids):
        note += CUT_DISAGREES.format(clips=clips, count=len(ids))
    record["measurement_notes"]["cut"] = note


def _apply_attempt(record: dict, runs_dir: Path, memory_key: str, cell_dir: Path) -> None:
    attempt = latest_attempt(runs_dir, memory_key)
    if attempt is None:
        return
    cut = read_cut(attempt)
    record["cut"] = cut
    record["selected_asset_ids"] = [shot["asset_id"] for shot in cut["selected"]]
    record["losses"] = read_losses(attempt)
    record["contract"] = read_contract_health(attempt, _generate_output(cell_dir))
    record.setdefault("hosted_usage", {})["images_sent"] = read_images_sent(attempt)
    apply_exact_usage(record["hosted_usage"], attempt)


# Two numbers only the process that ran the cell could have counted: the Mac lane
# times its own children, and no file under the cell directory holds either. A
# second read of that directory keeps what the run measured rather than
# publishing it as unmeasured.
_LIVE_ONLY_TIMINGS = ("peak_rss_mb", "cpu_s")
# The homebase marker is the same kind of fact, and the trap is sharper: the plan
# a recapture builds is today's and the directory it reads is the run's.
# `k8s-gpu-t1000` was cut before anything pinned a home, and publishing today's
# answer over it would claim the lanes agreed about a row made at Null Island.
UNKNOWN_HOMEBASE = "unknown"


def recapture_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """Read a cell's own directory again and rebuild its record, running nothing.

    The capture is the half of a run that keeps changing: a parser learns to read
    something the last one could not, and a cluster cell costs hours to run again
    to apply it. `k8s-gpu-t1000` is the case this was written for -- its film and
    its logs were on this machine, and only the reading of them was wrong.
    """
    cell_dir = out_dir / item.cell.id
    record = _new_record(item, primed=None, model=_reader_model(item, plan))
    if item.cell.lane == "mac":
        _apply_phase_logs(record, cell_dir, _LOCAL_PHASE_LOGS)
        generate = cell_dir / "generate.stdout.log"
        if generate.is_file():
            _apply_run_summary(record, generate.read_text(), cell_dir)
        cache = Path(_substitute(item.cache_dir, plan.environment)).expanduser()
        _apply_attempt(record, cache / EDITORIAL_RUNS, item.cell.id, cell_dir)
    else:
        _read_remote_artifacts(record, cell_dir)
        _apply_stranded_summary(record, cell_dir)
        _apply_attempt(record, cell_dir / REMOTE_ATTEMPTS, item.cell.id, cell_dir)
    _apply_stranded_cut(record, cell_dir)
    previous = _previous_record(cell_dir)
    _carry_live_timings(record, previous)
    record["homebase"] = previous.get("homebase", UNKNOWN_HOMEBASE)
    return record


def _previous_record(cell_dir: Path) -> dict:
    path = cell_dir / CELL_RECORD
    try:
        return json.loads(path.read_text()) if path.is_file() else {}
    except ValueError:
        return {}


def _carry_live_timings(record: dict, previous: dict) -> None:
    timing = previous.get("timing") or {}
    for field in _LIVE_ONLY_TIMINGS:
        if record["timing"][field] is None and timing.get(field) is not None:
            record["timing"][field] = timing[field]
            record["measurement_notes"].pop(field, None)


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


def load_operator_credentials(plan: Plan, items: Iterable[CellPlan], config: Path | None) -> None:
    """Read the operator's Immich into the plan once, if any of `items` runs against it.

    A cell with `operator_immich` has its manifests rendered with that server's
    address, so this has to happen before its config is written by anything: the
    run itself or a reader probe. The probe skipped it, and every probe of such a
    cell died on `KeyError: 'url'` before its first request.
    """
    if plan.operator_credentials or not any(item.operator_immich for item in items):
        return
    url, api_key = read_operator_immich(config)
    plan.operator_credentials.update({"url": url, "api_key": api_key})


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
        "--recapture",
        action="store_true",
        help="read the named cells' own directories under --out again and rebuild their"
        " records from what is in them, running nothing. Needs --cell or --lane, because"
        " it rewrites the records it touches.",
    )
    parser.add_argument(
        "--probe-readers-only",
        action="store_true",
        help="probe image and text support, projected time and cost, and run nothing else."
        " The same gate runs at the head of every reader cell.",
    )
    parser.add_argument(
        "--allow-reader-budget-overrun",
        action="append",
        default=[],
        metavar="CELL",
        help="waive the projected time/cost ceiling for this cell; repeatable."
        " Image support and readable answers are still required.",
    )
    parser.add_argument(
        "--serve-fixture", action="store_true", help="serve the fixture library on the LAN"
    )
    parser.add_argument(
        "--fresh-cache",
        action="store_true",
        help="empty each cell's editorial cache before it prepares, so prepare_cold_s is a"
        " first derivation rather than a replay. The shared models are never touched.",
    )
    parser.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    parser.add_argument(
        "--inference-tag",
        default=None,
        help="image tag for the inference service; defaults to --image-tag",
    )
    parser.add_argument("--inference-device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--inference-node-product",
        default="",
        help="pin the inference service to one card by its nvidia.com/gpu.product node label,"
        " for a run comparing what the service costs on each. Rendered into the overlay at"
        " apply time and never committed. Unset, the scheduler picks and the run records"
        " which card it got.",
    )
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


def _chosen_cells(manifest: dict, opts: argparse.Namespace, environment: dict[str, str]) -> tuple:
    """The cells this request covers, before a plan exists.

    Needed early: whether the LoadBalancer address has to be derived decides what
    goes into the environment the plan is built from.
    """
    lanes, ids = _lanes(opts.lane), tuple(opts.cell)
    return tuple(
        cell
        for cell in expand_cells(read_cells(manifest), environment)
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
    chosen = _chosen_cells(manifest, opts, environment)
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
            fresh_cache=opts.fresh_cache,
        )
        # Only for a request that will run something. Rebuilding a table off
        # records already on disk reads no config and needs no home.
        if not (opts.summarize_only or opts.recapture):
            check_homebase(manifest, environment)
        from setup_matrix_probe_readers import configure_reader_probes

        plan = configure_reader_probes(
            plan,
            config_source=opts.config,
            env_files=opts.env_file,
            budget_overrides=opts.allow_reader_budget_overrun,
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
            node_product=opts.inference_node_product,
        )
        if needs_overlay
        else ()
    )
    # The overlays a cell declared it cannot run without. A cell whose overlay is
    # not in this tree was already skipped by the planner, so nothing here asks
    # the cluster for a directory that does not exist.
    declared = required_overlays(tuple(item.cell for item in plan.runnable))

    if opts.dry_run:
        declared_steps = declared_overlay_steps(
            declared, device=opts.inference_device, keep=opts.keep_service
        )
        print(dry_run_text(plan, overlay=(*overlay, *declared_steps)))
        _report_skips(plan)
        return 0

    if opts.probe_readers_only:
        from setup_matrix_probe_readers import probe_cells

        _report_skips(plan)
        return probe_cells(
            plan,
            opts.config,
            manifest.get("pricing") or {},
            budget=manifest["libraries"][plan.library].get("reader_budget", {}),
            budget_overrides=opts.allow_reader_budget_overrun,
        )

    if plan.anonymize_required and not opts.anonymize:
        print(
            f"error: library {plan.library!r} is private. Pass --anonymize before publishing it.",
            file=sys.stderr,
        )
        return 2

    if opts.recapture:
        return _recapture(plan, opts, out_dir)

    if opts.summarize_only:
        return _summarize(plan, opts, out_dir)

    try:
        return _execute(plan, opts, out_dir, overlay, declared)
    finally:
        if server is not None:
            server.close()


def _execute(
    plan: Plan,
    opts: argparse.Namespace,
    out_dir: Path,
    overlay: tuple,
    declared: tuple[str, ...],
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    # The service comes up before any config is written, because a NAS cell's
    # config has to carry the address the LoadBalancer hands out and there is no
    # way to know it until the Service exists.
    device = _resolve_device(opts.inference_device, plan) if overlay else "cpu"
    lan = plan.environment.get(INFERENCE_ENV) == DERIVED_ADDRESS
    served = inference_image(opts.inference_tag or opts.image_tag, device=device) if overlay else ""
    warmup: float | None = None
    served_on: str | None = None
    # The captioner runs on whatever the probe found for the inference service:
    # one card in the cluster, and no cell asked for a device of its own.
    declared = declared_for_device(declared, device)
    caption_device = device if any(path in CAPTIONER_OVERLAYS for path in declared) else None
    caption_warmup = _bring_up_declared(plan, declared)
    if overlay:
        print(f"inference overlay: {overlay_path(device)} running {served}")
        bring_up_inference(plan, overlay_path(device), served, opts.inference_node_product)
        served_on = read_inference_gpu_product(plan)
        if served_on:
            print(f"inference is on {served_on}")
        if lan:
            _apply_overlay(plan, LAN_OVERLAY, up=True)
            plan.environment[INFERENCE_ENV] = f"http://{_lan_address(plan)}:{INFERENCE_PORT}"
            print(f"inference reachable off-cluster on port {INFERENCE_PORT}")
    # Every lane that reads its picture facts from the service waits on this one
    # request, wherever the service came from.
    if any(item.cell.facts == "service" for item in plan.runnable):
        warmup = warm_inference(plan)
        print(f"inference answered a facts request after {warmup:.0f}s")

    load_operator_credentials(plan, plan.runnable, opts.config)
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

    if not opts.keep_service:
        # The declared overlays first: they are what the cells called, and the
        # inference service is what those overlays called in turn.
        for path in declared:
            _apply_overlay(plan, path, up=False)
        if overlay:
            if lan:
                _apply_overlay(plan, LAN_OVERLAY, up=False)
            _apply_overlay(plan, overlay_path(device), up=False)
    cluster_cells = tuple(item.cell for item in plan.runnable if item.cell.lane == "k8s")
    if opts.purge_claims and cluster_cells:
        _run_bare(purge_claims_command(cluster_cells), plan)

    _publish_summary(
        plan,
        opts,
        out_dir,
        _collect_rows(out_dir, records, plan.environment),
        warmup,
        served_on,
        caption_warmup,
        caption_device,
    )
    _report_skips(plan)
    return 0 if all(row.get("error") is None for row in records) else 1


def _collect_rows(out_dir: Path, fresh: list[dict], environment: dict[str, str]) -> list[dict]:
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
    order = [cell.id for cell in expand_cells(read_cells(load_manifest()), environment)]
    return sorted(rows.values(), key=lambda row: _manifest_rank(order, row["id"]))


def _manifest_rank(order: list[str], cell_id: str) -> int:
    return order.index(cell_id) if cell_id in order else len(order)


def _publish_summary(
    plan: Plan,
    opts: argparse.Namespace,
    out_dir: Path,
    rows: list[dict],
    warmup: float | None = None,
    inference_gpu_product: str | None = None,
    captioner_warmup: float | None = None,
    captioner_device: str | None = None,
) -> None:
    summary = build_summary(
        library=plan.library,
        month=plan.month,
        image=plan.image,
        rows=rows,
        inference_warmup_s=warmup,
        inference_gpu_product=inference_gpu_product,
        pricing=load_manifest().get("pricing") or {},
        captioner_warmup_s=captioner_warmup,
        captioner_device=captioner_device,
    )
    if opts.anonymize:
        summary = anonymize(summary)
    (out_dir / "summary.data.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "summary.md").write_text(build_markdown(summary))
    print(f"\n{out_dir / 'summary.md'}")


def _recapture(plan: Plan, opts: argparse.Namespace, out_dir: Path) -> int:
    """Read the named cells' directories again, rewrite their records, and republish."""
    if not (opts.cell or opts.lane):
        print("error: --recapture rewrites records, so name --cell or --lane", file=sys.stderr)
        return 2
    rows = [
        recapture_cell(item, plan, out_dir)
        for item in plan.runnable
        if (out_dir / item.cell.id).is_dir()
    ]
    if not rows:
        print(f"error: no cell of this request has a directory under {out_dir}", file=sys.stderr)
        return 2
    for row in rows:
        print(f"recaptured {row['id']}: {len(row['selected_asset_ids'])} kept")
    _publish_summary(plan, opts, out_dir, _collect_rows(out_dir, rows, plan.environment))
    return 0


def _summarize(plan: Plan, opts: argparse.Namespace, out_dir: Path) -> int:
    """Rebuild the two published files from the cells already on disk, running nothing."""
    rows = _collect_rows(out_dir, [], plan.environment)
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


def read_inference_gpu_product(plan: Plan) -> str:
    """Which card the inference pod landed on, off the node it was scheduled to.

    Asked of the pod and not of the Deployment: with no `--inference-node-product`
    the scheduler picks, and a table comparing two cards has to say which one was
    answering the facts requests underneath every service row. A cluster with no
    GPU answers nothing here, which is the honest empty.
    """
    node = _query(plan, inference_node_command())
    return _query(plan, node_product_command(node)) if node else ""


def _query(plan: Plan, command: tuple[str, ...]) -> str:
    proc = subprocess.run(  # noqa: S603
        [_substitute(part, plan.environment) for part in command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _bring_up_declared(plan: Plan, declared: tuple[str, ...]) -> float | None:
    """Apply the overlays the cells declared, and wait on the one with a server behind it.

    Applying the captioner overlay is not the same as having it, and it is the
    only declared overlay that serves anything: the rest are up the moment `apply`
    returns. Returns how long the caption server took, or None when no cell in
    this run asked for one.
    """
    for path in declared:
        _apply_overlay(plan, path, up=True)
    captioner = next((path for path in declared if path in CAPTIONER_OVERLAYS), "")
    if not captioner:
        return None
    print(f"captioner overlay: {captioner}")
    warmup = bring_up_captioner(plan)
    print(f"captioner answered a caption request after {warmup:.0f}s")
    return warmup


def bring_up_captioner(plan: Plan) -> float:
    """Wait out the captioner's rollout, then make it caption something.

    Two waits because there are two things to wait for, and neither implies the
    other. The init container fetches half a gigabyte of GGUF onto a claim that is
    empty the first time a cluster runs the full tier, which is what `rollout
    status` sits through. Then llama.cpp maps the weights, and only after that is
    there an endpoint advertising the alias a `tier: full` cell refuses to work
    without. Without either, `--cell k8s-full-rules` on its own asked for a
    caption before the server existed and died on its first picture.
    """
    if _run_bare(CAPTIONER_ROLLOUT, plan).returncode != 0:
        raise SystemExit(
            f"{CAPTIONER_DEPLOYMENT} never rolled out within {CAPTIONER_ROLLOUT_TIMEOUT}, so "
            "the full-tier cells would have asked an absent server for every caption."
        )
    kubectl = tuple(_substitute(part, plan.environment) for part in KUBECTL)
    return await_captions_via_forward(kubectl, CAPTIONER_SERVICE, CAPTIONER_PORT)


def bring_up_inference(plan: Plan, path: str, image: str, node_product: str = "") -> None:
    """Apply the inference overlay and come back only once its new pod can be reached.

    `apply` returns as soon as the API server has the manifest. When the tag has
    changed, the Deployment is pulling by then, and a Service with no ready
    endpoint answers nothing at all: a `port-forward` to one never even gets a
    local listener. That is not a slow service, and the warm-up's fifteen minutes
    are not for it, so the pull is waited out here where `rollout status` is the
    thing that says what happened.
    """
    _apply_overlay(plan, path, up=True, image=image, node_product=node_product)
    if _run_bare(INFERENCE_ROLLOUT, plan).returncode != 0:
        raise SystemExit(
            f"{INFERENCE_DEPLOYMENT} never rolled out within {INFERENCE_ROLLOUT_TIMEOUT}, so "
            "nothing can reach the inference service and no cell would have measured it."
        )


def _apply_overlay(
    plan: Plan, path: str, *, up: bool, image: str = "", node_product: str = ""
) -> None:
    if not up:
        _run_bare((*KUBECTL, "delete", "-k", path, "--ignore-not-found"), plan)
    elif image:
        rendered = retag_inference(_render_overlay(path), image)
        _apply_rendered(plan, pin_inference_node(rendered, node_product))
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
