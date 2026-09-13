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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_pinned_config import pinned_config  # noqa: E402
from setup_matrix_capture import (  # noqa: E402
    anonymize,
    latest_attempt,
    parse_cgroup_cpu_seconds,
    parse_cgroup_peak_rss_mb,
    parse_prepare_seconds,
    parse_prepared_pictures,
    parse_run_summary,
    probe_video,
    read_cut,
    read_losses,
)
from setup_matrix_plan import (  # noqa: E402
    FIXTURE_ENV,
    FIXTURE_PORT,
    CellPlan,
    Plan,
    PlanError,
    build_plan,
    dry_run_text,
    inference_overlay_steps,
    load_manifest,
    overlay_path,
)
from setup_matrix_summary import build_markdown, build_summary  # noqa: E402

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
    """Put the stock June 2024 library on the LAN and return the URL to point cells at."""
    sys.path.insert(0, str(REPO_ROOT))
    from tests.e2e.fake_immich import FakeImmichServer

    server = FakeImmichServer.start(root, host="0.0.0.0", port=FIXTURE_PORT)  # noqa: S104
    return server, f"http://{lan_address()}:{FIXTURE_PORT}"


def _substitute(part: str, environment: dict[str, str]) -> str:
    """Put the environment's values back into a command rendered with `$NAME` references."""
    for name, value in sorted(environment.items(), key=lambda item: -len(item[0])):
        if f"${name}" in part:
            part = part.replace(f"${name}", value)
    return part


def _run_step(command: tuple[str, ...], plan: Plan, item: CellPlan) -> subprocess.CompletedProcess:
    resolved = [_substitute(part, plan.environment) for part in command]
    passthrough = {
        name: plan.environment[name] for name in item.app_credentials if name in plan.environment
    }
    return subprocess.run(  # noqa: S603
        resolved,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **passthrough},
        check=False,
    )


def _child_cost() -> tuple[float, float]:
    """Peak RSS in MB and CPU seconds charged to children so far, from the kernel."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    divisor = 1_048_576 if sys.platform == "darwin" else 1024
    return usage.ru_maxrss / divisor, usage.ru_utime + usage.ru_stime


def _bank_primed(cache_dir: Path, memory_key: str) -> bool:
    """Whether this scope had already been prepared, which makes `cold` a re-read."""
    return (Path(cache_dir).expanduser() / "editorial-runs" / memory_key).is_dir()


def run_local_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """The mac lane: three invocations here in this process tree, timed and captured."""
    cell_dir = out_dir / item.cell.id
    cell_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(_substitute(item.cache_dir, plan.environment))
    record = _new_record(item, primed=_bank_primed(cache, item.cell.id))
    before_rss, before_cpu = _child_cost()

    for step in item.steps:
        started = time.monotonic()
        proc = _run_step(step.command, plan, item)
        elapsed = time.monotonic() - started
        (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
        (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
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

    after_rss, after_cpu = _child_cost()
    record["timing"]["peak_rss_mb"] = round(max(after_rss, before_rss), 1)
    record["timing"]["cpu_s"] = round(after_cpu - before_cpu, 2)
    _apply_attempt(record, cache, item.cell.id)
    return record


def run_remote_cell(item: CellPlan, plan: Plan, out_dir: Path) -> dict:
    """The NAS and cluster lanes: push, run, pull, then read the same artifacts back."""
    cell_dir = out_dir / item.cell.id
    cell_dir.mkdir(parents=True, exist_ok=True)
    record = _new_record(item, primed=None)
    for step in item.steps:
        proc = _run_step(step.command, plan, item)
        (cell_dir / f"{step.name}.stdout.log").write_text(proc.stdout or "")
        (cell_dir / f"{step.name}.stderr.log").write_text(proc.stderr or "")
        if proc.returncode != 0 and step.name not in {"logs", "delete"}:
            record["error"] = f"{step.name} exited {proc.returncode}"
            break

    _read_remote_artifacts(record, cell_dir)
    return record


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
    generate = cell_dir / "generate.log"
    if generate.is_file():
        _apply_run_summary(record, generate.read_text(), cell_dir)
    peak = cell_dir / "peak-rss-bytes.txt"
    if peak.is_file():
        record["timing"]["peak_rss_mb"] = parse_cgroup_peak_rss_mb(peak.read_text())
    cpu = cell_dir / "cpu.txt"
    if cpu.is_file():
        record["timing"]["cpu_s"] = parse_cgroup_cpu_seconds(cpu.read_text())


def _apply_prepared(record: dict, text: str) -> None:
    pictures, per_picture = parse_prepared_pictures(text)
    record["prepared"] = {"pictures": pictures, "seconds_per_picture": per_picture}


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
        "timing": {
            "prepare_cold_s": None,
            "prepare_warm_s": None,
            "selection_s": None,
            "render_s": None,
            "total_s": None,
            "peak_rss_mb": None,
            "cpu_s": None,
        },
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
    record["timing"]["selection_s"] = summary.selection_s
    record["timing"]["render_s"] = summary.render_s
    record["timing"]["total_s"] = summary.total_s
    record["planned"] = summary.planned
    record["eligible"] = summary.eligible
    record["hosted_usage"] = summary.usage.as_dict()
    if summary.video_path:
        local = cell_dir / Path(summary.video_path).name
        source = Path(summary.video_path)
        record["video"] = probe_video(source if source.is_file() else local) or {}
        record["video"]["path"] = str(source)


def _apply_attempt(record: dict, cache: Path, memory_key: str) -> None:
    attempt = latest_attempt(cache, memory_key)
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


def _write_cell_config(item: CellPlan, plan: Plan, out_dir: Path, config: Path | None) -> None:
    pins = {
        key: _substitute(value, plan.environment) if isinstance(value, str) else value
        for key, value in item.pins.items()
    }
    pinned_config(config, out_dir / item.cell.id / "config.yaml", pins)
    for name, body in item.manifests.items():
        rendered = _substitute(body, plan.environment)
        (out_dir / item.cell.id / name).write_text(rendered)


def run_lane(lane: str, items: list[CellPlan], plan: Plan, out_dir: Path) -> list[dict]:
    runner = run_local_cell if lane == "mac" else run_remote_cell
    if lane == "mac":
        # One heavy job at a time, and the only lane allowed to call the local
        # model server: the Mac kernel-panicked under parallel load.
        return [runner(item, plan, out_dir) for item in items]
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(lambda item: runner(item, plan, out_dir), items))


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
        "--serve-fixture", action="store_true", help="serve the fixture library on the LAN"
    )
    parser.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG)
    parser.add_argument("--inference-device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--keep-service", action="store_true", help="leave the inference overlay running"
    )
    return parser.parse_args(argv)


def _lanes(requested: list[str]) -> tuple[str, ...]:
    return () if not requested or "all" in requested else tuple(dict.fromkeys(requested))


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

    try:
        plan = build_plan(
            manifest=load_manifest(),
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

    needs_overlay = any(item.cell.inference_overlay for item in plan.runnable)
    overlay = (
        inference_overlay_steps(device=opts.inference_device, keep=opts.keep_service)
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

    try:
        return _execute(plan, opts, out_dir, overlay)
    finally:
        if server is not None:
            server.close()


def _execute(plan: Plan, opts: argparse.Namespace, out_dir: Path, overlay: tuple) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in plan.runnable:
        _write_cell_config(item, plan, out_dir, opts.config)

    device = _resolve_device(opts.inference_device, plan) if overlay else "cpu"
    if overlay:
        print(f"inference overlay: {overlay_path(device)}")
        _apply_overlay(plan, device, up=True)

    records: list[dict] = [
        {**_new_record(item, primed=None), "skip_reason": item.skip_reason} for item in plan.skipped
    ]
    by_lane: dict[str, list[CellPlan]] = {}
    for item in plan.runnable:
        by_lane.setdefault(item.cell.lane, []).append(item)

    # Lanes run together; inside the mac lane, cells do not.
    with ThreadPoolExecutor(max_workers=max(len(by_lane), 1)) as pool:
        futures = [
            pool.submit(run_lane, lane, items, plan, out_dir) for lane, items in by_lane.items()
        ]
        for future in futures:
            records.extend(future.result())

    if overlay and not opts.keep_service:
        _apply_overlay(plan, device, up=False)

    order = [cell.cell.id for cell in plan.cells]
    records.sort(key=lambda row: order.index(row["id"]))
    summary = build_summary(library=plan.library, month=plan.month, image=plan.image, rows=records)
    if opts.anonymize:
        summary = anonymize(summary)
    (out_dir / "summary.data.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "summary.md").write_text(build_markdown(summary))
    print(f"\n{out_dir / 'summary.md'}")
    _report_skips(plan)
    return 0 if all(row.get("error") is None for row in records) else 1


def _apply_overlay(plan: Plan, device: str, *, up: bool) -> None:
    path = overlay_path(device)
    verb = ["apply", "-k", path] if up else ["delete", "-k", path, "--ignore-not-found"]
    command = ("kubectl", "--context", "$MATRIX_K8S_CONTEXT", "-n", "$MATRIX_K8S_NAMESPACE", *verb)
    subprocess.run(  # noqa: S603
        [_substitute(part, plan.environment) for part in command],
        cwd=REPO_ROOT,
        check=False,
    )


def _report_skips(plan: Plan) -> None:
    for item in plan.skipped:
        print(f"skipped {item.cell.id}: {item.skip_reason}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
