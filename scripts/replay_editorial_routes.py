#!/usr/bin/env python3
"""Replay editorial routes warm through the public CLI and compare against a reference.

The reference file lives outside the repository (it carries route arguments and
attempt paths from a private library). For every route it runs
`immich-memories --config <config> generate <args> --no-render --no-music` in a
subprocess where every HTTP request to the configured model providers is refused,
so a route that needs a fresh judgment fails instead of silently going cold.
Immich itself stays reachable. The newest attempt written under the cache root is
then compared with the accepted one: plan bytes, ordered carrier asset ids, and the
decision projection. Judgment-bank rows are counted before and after; a warm run
adds none.

Usage:
    python scripts/replay_editorial_routes.py --reference ~/.immich-memories-matrix/story-first-reference.private.json
    python scripts/replay_editorial_routes.py --reference ... --routes monthly,year --seeds 11
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

DECISION_FIELDS = (
    "contract_key",
    "intent",
    "target_seconds",
    "slots_total",
    "tiers",
    "chapters",
    "carriers",
    "content_seconds",
    "duration_realization",
    "story",
)

# Runs inside the child process: refuse provider hosts, pass everything else through.
_CHILD = """
import os, sys
from urllib.parse import urlparse
import httpx
blocked = {host for host in os.environ.get("IMMICH_MEMORIES_PARITY_BLOCK_HOSTS", "").split(",") if host}
_send, _asend = httpx.Client.send, httpx.AsyncClient.send
def _host(request):
    return urlparse(str(request.url)).netloc.lower()
def send(self, request, *args, **kwargs):
    if _host(request) in blocked:
        print("PARITY_BLOCKED_HTTP " + _host(request), file=sys.stderr, flush=True)
        raise RuntimeError("warm replay attempted a provider request")
    return _send(self, request, *args, **kwargs)
async def asend(self, request, *args, **kwargs):
    if _host(request) in blocked:
        print("PARITY_BLOCKED_HTTP " + _host(request), file=sys.stderr, flush=True)
        raise RuntimeError("warm replay attempted a provider request")
    return await _asend(self, request, *args, **kwargs)
httpx.Client.send, httpx.AsyncClient.send = send, asend
from immich_memories.cli import main
sys.argv = ["immich-memories", *sys.argv[1:]]
main()
"""


@dataclass(frozen=True)
class RouteOutcome:
    route: str
    seed: str
    status: str
    seconds: float
    blocked_calls: int
    bank_rows_added: int
    attempt_dir: str | None
    plan_sha256: str | None
    carriers: int | None
    carriers_identical: bool | None
    decision_sha256: str | None
    detail: str = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def provider_hosts(config_path: Path) -> set[str]:
    """Hosts that must not be contacted: every configured model endpoint."""
    import yaml

    document = yaml.safe_load(config_path.read_text()) or {}
    advanced = document.get("advanced") or {}
    candidates = []
    for section in ("llm", "description_llm", "title_llm"):
        block = document.get(section) or advanced.get(section) or {}
        candidates.append(block.get("base_url"))
    editorial = document.get("editorial") or advanced.get("editorial") or {}
    candidates.append(
        (editorial.get("preparation") or {}).get("caption_base_url") or "http://localhost:8092/v1"
    )
    hosts = set()
    for url in candidates:
        if url:
            hosts.add(urlparse(str(url)).netloc.lower())
    return hosts


BANK_TABLE_MARKERS = ("judg", "request", "verdict", "gateway", "reading", "insight", "vote")


def _rows_in(path: Path) -> int:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = [
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
            if any(marker in row[0] for marker in BANK_TABLE_MARKERS)
        ]
        return sum(
            connection.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608
            for table in tables
        )


def bank_rows(cache_root: Path, config_path: Path | None = None) -> int:
    """Rows in every judgment bank a warm replay must leave untouched."""
    paths = [cache_root / "judgments.db"]
    if config_path is not None:
        import yaml

        document = yaml.safe_load(config_path.read_text()) or {}
        advanced = document.get("advanced") or {}
        editorial = document.get("editorial") or advanced.get("editorial") or {}
        if editorial.get("annotation_database"):
            paths.append(
                Path(os.path.expandvars(str(editorial["annotation_database"]))).expanduser()
            )
    return sum(_rows_in(path) for path in paths if path.exists())


def newest_attempt(cache_root: Path, started_after: float) -> Path | None:
    """The attempt directory written by the run that started at `started_after`."""
    newest: tuple[float, Path] | None = None
    for pointer in (cache_root / "editorial-runs").glob("*/latest-attempt.private.json"):
        try:
            directory = Path(json.loads(pointer.read_text())["directory"])
            status = directory / "status.private.json"
            modified = status.stat().st_mtime
        except (OSError, ValueError, KeyError):
            continue
        if modified >= started_after and (newest is None or modified > newest[0]):
            newest = (modified, directory)
    return None if newest is None else newest[1]


def carrier_ids(plan: dict) -> list[str]:
    return [str(carrier.get("asset_id")) for carrier in plan.get("carriers") or ()]


def decision_sha256(plan: dict) -> str:
    projection = {field: plan.get(field) for field in DECISION_FIELDS if field in plan}
    return sha256_bytes(
        json.dumps(projection, sort_keys=True, separators=(",", ":"), default=str).encode()
    )


def baseline_of(route: dict) -> dict | None:
    """The record a replay must reproduce: the HEAD baseline when banked, else the accepted run."""
    head = route.get("head_baseline")
    if isinstance(head, dict) and head.get("carrier_asset_ids"):
        return head
    if route.get("carrier_asset_ids"):
        return {
            "plan_sha256": route.get("accepted_plan_sha256"),
            "carrier_asset_ids": route.get("carrier_asset_ids"),
        }
    return None


def reference_routes(reference: dict) -> dict[str, dict]:
    """Every entry that carries route arguments; metadata keys in the file are ignored."""
    routes = reference.get("routes", reference)
    return {
        key: value
        for key, value in routes.items()
        if isinstance(value, dict) and (value.get("generate_args") or value.get("route_args"))
    }


def compare_plan(plan_path: Path, baseline: dict) -> tuple[str, int, bool, str]:
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    ids = carrier_ids(plan)
    return (
        sha256_bytes(raw),
        len(ids),
        ids == list(baseline.get("carrier_asset_ids") or []),
        decision_sha256(plan),
    )


_HARNESS_OWNED = {"--no-render", "--no-music", "--quiet"}


def route_argv(route: dict) -> list[str]:
    """The route's own `generate` arguments, without what the harness sets itself."""
    raw = list(route.get("route_args") or route.get("generate_args") or [])
    if raw and raw[0] == "generate":
        raw = raw[1:]
    argv: list[str] = []
    skip_value = False
    for token in raw:
        if skip_value:
            skip_value = False
            continue
        if token in ("--output", "-o"):
            skip_value = True
            continue
        if token in _HARNESS_OWNED:
            continue
        argv.append(token)
    return argv


def run_route(
    key: str, route: dict, *, seed: str, python: str, out: Path, timeout: int
) -> RouteOutcome:
    config_path = Path(route["config_path"]).expanduser()
    cache_root = Path(route["cache_root"]).expanduser()
    baseline = baseline_of(route)
    if baseline is None:
        return RouteOutcome(
            key,
            seed,
            "no-baseline",
            0.0,
            0,
            0,
            None,
            None,
            None,
            None,
            None,
            "no banked carriers to compare against",
        )
    hosts = provider_hosts(config_path)
    env = os.environ | {
        "PYTHONHASHSEED": seed,
        "IMMICH_MEMORIES_PARITY_BLOCK_HOSTS": ",".join(sorted(hosts)),
    }
    rows_before = bank_rows(cache_root, config_path)
    started = time.time()
    log = out / f"{key}-seed{seed}.log"
    command = [
        python,
        "-c",
        _CHILD,
        "--config",
        str(config_path),
        "generate",
        *route_argv(route),
        "--no-render",
        "--no-music",
        "--quiet",
        "--output",
        str(out / f"{key}-seed{seed}"),
    ]
    with log.open("w") as handle:
        completed = subprocess.run(  # noqa: S603
            command, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout
        )
    seconds = round(time.time() - started, 1)
    text = log.read_text(errors="replace")
    blocked = text.count("PARITY_BLOCKED_HTTP")
    added = bank_rows(cache_root, config_path) - rows_before
    attempt = newest_attempt(cache_root, started - 1)
    if blocked:
        return RouteOutcome(
            key,
            seed,
            "cold-needed",
            seconds,
            blocked,
            added,
            None,
            None,
            None,
            None,
            None,
            "provider request refused",
        )
    if completed.returncode != 0:
        return RouteOutcome(
            key,
            seed,
            "failed",
            seconds,
            0,
            added,
            None,
            None,
            None,
            None,
            None,
            f"exit {completed.returncode}; see {log.name}",
        )
    if attempt is None or not (attempt / "plan.private.json").exists():
        return RouteOutcome(
            key,
            seed,
            "no-attempt",
            seconds,
            0,
            added,
            None,
            None,
            None,
            None,
            None,
            "no editorial attempt written",
        )
    plan_sha, count, same, decision = compare_plan(attempt / "plan.private.json", baseline)
    if plan_sha == baseline.get("plan_sha256"):
        status = "identical"
    elif same:
        status = "same-carriers"
    else:
        status = "changed"
    if added:
        status = f"{status}+bank-growth"
    return RouteOutcome(
        key, seed, status, seconds, 0, added, str(attempt), plan_sha, count, same, decision
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--routes", default="", help="comma-separated route keys; default all")
    parser.add_argument("--seeds", default="11,97", help="PYTHONHASHSEED values, one run each")
    parser.add_argument(
        "--out", type=Path, default=Path.home() / ".immich-memories-matrix" / "parity"
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--allow-no-attempt",
        action="store_true",
        help="before the route cut, the CLI writes no attempt",
    )
    args = parser.parse_args()

    reference = json.loads(args.reference.expanduser().read_text())
    routes = reference_routes(reference)
    wanted = [key for key in args.routes.split(",") if key] or list(routes)
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    outcomes: list[RouteOutcome] = []
    for key in wanted:
        for seed in args.seeds.split(","):
            outcome = run_route(
                key, routes[key], seed=seed, python=args.python, out=args.out, timeout=args.timeout
            )
            outcomes.append(outcome)
            print(
                f"{outcome.route:14s} seed={seed:3s} {outcome.status:18s} {outcome.seconds:7.1f}s blocked={outcome.blocked_calls} bank+={outcome.bank_rows_added} carriers={outcome.carriers} {outcome.detail}"
            )
    report = args.out / f"report-{time.strftime('%Y%m%dT%H%M%S')}.private.json"
    report.write_text(json.dumps([asdict(o) for o in outcomes], indent=2) + "\n")
    report.chmod(0o600)
    accepted = {"identical", "same-carriers", "no-baseline"} | (
        {"no-attempt"} if args.allow_no_attempt else set()
    )
    failures = [o for o in outcomes if o.status not in accepted]
    print(f"parity: {len(outcomes) - len(failures)}/{len(outcomes)} accepted; report {report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
