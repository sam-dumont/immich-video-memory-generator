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
adds none. When the carriers differ and the reference names the attempt the baseline
came from (`head_baseline.attempt_dir`, else `accepted_attempt_dir`), the two attempts'
`evidence-hashes.json` files are diffed as well, so the report names the first episode
whose evidence moved and how many of its asset lines changed.

A cut has two ways to move, and the harness separates them. Every baseline carries a
fingerprint of the judgment banks as they stood when it was banked, so a replay whose
carriers differ reports `store-moved` — naming the tables that grew — instead of
`changed`, which would read as a code regression. `--bank` writes that record: it
replaces the replayed routes' HEAD baselines with what the session produced, keeping a
timestamped copy of the reference it replaced.

A route scoped to "today" can only replay when the reference pins it to the day the
accepted run was cut, with `"target_date": "YYYY-MM-DD"` on the route entry.

Usage:
    python scripts/replay_editorial_routes.py --reference ~/.immich-memories-matrix/story-first-reference.private.json
    python scripts/replay_editorial_routes.py --reference ... --routes monthly,year --seeds 11
    python scripts/replay_editorial_routes.py --reference ... --routes year --seeds 11 --bank
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_routes import pinned_day_args  # noqa: E402

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
    for section in ("llm", "title_llm"):
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
# Public-safe per-episode evidence provenance, written beside the plan in every attempt.
EVIDENCE_HASHES = "evidence-hashes.json"


def _table_shape(connection: sqlite3.Connection, table: str) -> list[int]:
    """How many rows the table holds, and how far its rowids have run.

    The high-water mark is the half that survives churn: a run that inserts a row
    and deletes another leaves the count alone, and the replay would read a store
    it cannot tell apart from the banked one.
    """
    count = connection.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608
    try:
        top = connection.execute(f"select coalesce(max(rowid), 0) from {table}").fetchone()[  # noqa: S608
            0
        ]
    except sqlite3.OperationalError:  # a WITHOUT ROWID table has no such column
        top = 0
    return [int(count), int(top)]


def _shapes_in(path: Path) -> dict[str, list[int]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {
            row[0]: _table_shape(connection, row[0])
            for row in connection.execute("select name from sqlite_master where type='table'")
            if any(marker in row[0] for marker in BANK_TABLE_MARKERS)
        }


def bank_paths(cache_root: Path, config_path: Path | None = None) -> list[Path]:
    """Every judgment bank a warm replay reads and must leave untouched."""
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
    return [path for path in paths if path.exists()]


def store_fingerprint(cache_root: Path, config_path: Path | None = None) -> dict[str, list[int]]:
    """What the banks looked like, by table name: `{table: [rows, highest rowid]}`.

    Banked beside a baseline so a later replay can separate the two ways a cut can
    move. Table names are schema, so the fingerprint is safe to print and to diff;
    nothing it carries comes from the library. Counts are cheap enough to take on
    every route — a digest of the rows themselves would cost minutes per run and
    answer the same question.
    """
    fingerprint: dict[str, list[int]] = {}
    for path in bank_paths(cache_root, config_path):
        for table, shape in _shapes_in(path).items():
            # Two banks may name a table alike; the sum is still the honest total.
            previous = fingerprint.get(table)
            fingerprint[table] = (
                shape if previous is None else [previous[0] + shape[0], max(previous[1], shape[1])]
            )
    return fingerprint


def bank_rows(cache_root: Path, config_path: Path | None = None) -> int:
    return sum(shape[0] for shape in store_fingerprint(cache_root, config_path).values())


def store_drift(banked: dict[str, list[int]], current: dict[str, list[int]]) -> str:
    """Name the bank tables that moved between banking and this replay, and by how much."""
    moved = [
        f"{table} {current.get(table, [0, 0])[0] - shape[0]:+d} rows"
        for table, shape in sorted(banked.items())
        if current.get(table) != shape
    ]
    gained = sorted(set(current) - set(banked))
    if gained:
        moved.append(f"{len(gained)} new table(s): {', '.join(gained)}")
    return "store moved since the baseline was banked: " + ("; ".join(moved) or "shape unchanged")


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
            "store_fingerprint": route.get("store_fingerprint"),
        }
    return None


def baseline_attempt_dir(route: dict) -> Path | None:
    """Where the attempt behind the baseline still lives, when the reference names it.

    `head_baseline.attempt_dir` wins over the accepted run's own directory, so a route
    whose HEAD baseline was rebanked diffs against the run its carriers actually came
    from. The accepted run spells it `accepted_attempt_dir`, which is the only name a
    route without a HEAD baseline has to offer.
    """
    head = route.get("head_baseline")
    named = (head.get("attempt_dir") if isinstance(head, dict) else None) or route.get(
        "accepted_attempt_dir"
    )
    return Path(str(named)).expanduser() if named else None


def evidence_rows(directory: Path) -> dict[str, dict]:
    """One attempt's per-episode evidence hashes, keyed by group, in written order."""
    try:
        document = json.loads((directory / EVIDENCE_HASHES).read_text())
    except (OSError, ValueError):
        return {}
    return {
        str(row["group_id"]): {
            "evidence_key": str(row.get("evidence_key") or ""),
            "assets": {
                str(asset["asset_id"]): str(asset["line_sha256"])
                for asset in row.get("assets") or ()
            },
        }
        for row in document.get("episodes") or ()
    }


def _evidence_gap(newest: Path, baseline: Path) -> str | None:
    """Why these two attempts cannot be diffed at all, or None when they can."""
    if not baseline.exists():
        return "the baseline attempt is gone from disk"
    if not (baseline / EVIDENCE_HASHES).exists():
        return f"the baseline predates {EVIDENCE_HASHES}"
    if not (newest / EVIDENCE_HASHES).exists():
        return f"this attempt wrote no {EVIDENCE_HASHES}"
    return None


def evidence_drift(newest: Path, baseline: Path) -> str:
    """Name the first episode whose evidence moved and how many of its lines changed.

    Ids and counts only: the rendered lines stay in the attempt's private sibling.
    A side that cannot be read says so — an empty detail would read as "nothing
    moved", which is exactly the wrong thing to tell someone chasing a changed cut.
    """
    gap = _evidence_gap(newest, baseline)
    if gap is not None:
        return f"no evidence diff: {gap}"
    new_rows, old_rows = evidence_rows(newest), evidence_rows(baseline)
    if not new_rows or not old_rows:
        return f"no evidence diff: an {EVIDENCE_HASHES} is present but unreadable"
    moved = [
        group
        for group, row in new_rows.items()
        if group in old_rows and row["evidence_key"] != old_rows[group]["evidence_key"]
    ]
    if not moved:
        gained = [group for group in new_rows if group not in old_rows]
        lost = [group for group in old_rows if group not in new_rows]
        if not gained and not lost:
            return "evidence identical"
        return f"episode membership moved: {len(gained)} new, {len(lost)} gone"
    first = moved[0]
    new_assets, old_assets = new_rows[first]["assets"], old_rows[first]["assets"]
    changed = [
        asset_id for asset_id, digest in new_assets.items() if old_assets.get(asset_id) != digest
    ]
    return (
        f"evidence moved in {len(moved)} episode(s); first {first}: "
        f"{len(changed)}/{len(new_assets)} line hashes changed"
        + (f", from {changed[0]}" if changed else " (membership differs)")
    )


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

# A route scoped to "today" asks a different question on every run and can never
# replay. The reference may pin it to the day the accepted sheet was cut, under the
# same key the matrix driver uses, and the pin expands the same way.
PINNED_DAY = "target_date"


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
    day = route.get(PINNED_DAY)
    return argv if not day else argv + pinned_day_args(str(day))


def verdict(
    *,
    plan_sha: str,
    same_carriers: bool,
    baseline: dict,
    route: dict,
    attempt: Path,
    store: dict[str, list[int]],
) -> tuple[str, str]:
    """The status this attempt earns, and the one line that says why it is not identical.

    A cut can move because the code moved or because the store underneath it did, and
    the two call for opposite responses. `store-moved` says which one this was instead
    of handing back `changed` and letting someone bisect the code for a week.
    """
    if plan_sha == baseline.get("plan_sha256"):
        return "identical", ""
    if same_carriers:
        return "same-carriers", ""
    banked = baseline.get("store_fingerprint")
    if not banked:
        status, note = (
            "changed",
            "store fingerprint not banked; store and code cannot be told apart",
        )
    elif banked != store:
        status, note = "store-moved", store_drift(banked, store)
    else:
        status, note = "changed", "store unchanged since the baseline was banked"
    accepted = baseline_attempt_dir(route)
    drift = "" if accepted is None else evidence_drift(attempt, accepted)
    return status, f"{note}; {drift}" if drift else note


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
    store_before = store_fingerprint(cache_root, config_path)
    rows_before = sum(shape[0] for shape in store_before.values())
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
    status, detail = verdict(
        plan_sha=plan_sha,
        same_carriers=same,
        baseline=baseline,
        route=route,
        attempt=attempt,
        store=store_before,
    )
    if added:
        status = f"{status}+bank-growth"
    return RouteOutcome(
        key, seed, status, seconds, 0, added, str(attempt), plan_sha, count, same, decision, detail
    )


# A rebank replaces the record a later replay must reproduce, so only a run that
# proved itself warm and reached a plan is allowed to become one.
BANKABLE = frozenset({"identical", "same-carriers", "changed", "store-moved"})


def banked_baseline(route: dict, outcome: RouteOutcome) -> dict:
    """What this warm replay produced, in the shape the next one reads back.

    The store fingerprint travels with the carriers: without it the next replay can
    only say the cut changed, never whether anything but the code was different.
    """
    attempt = Path(str(outcome.attempt_dir))
    raw = (attempt / "plan.private.json").read_bytes()
    plan = json.loads(raw)
    ids = carrier_ids(plan)
    return {
        "attempt_dir": str(attempt),
        "banked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "carrier_asset_ids": ids,
        "carrier_count": len(ids),
        "content_seconds": plan.get("content_seconds"),
        "plan_sha256": sha256_bytes(raw),
        "provider_calls": outcome.blocked_calls,
        "seconds": outcome.seconds,
        "store_fingerprint": store_fingerprint(
            Path(route["cache_root"]).expanduser(), Path(route["config_path"]).expanduser()
        ),
    }


def bank_refusal(outcomes: list[RouteOutcome]) -> str:
    """Why this route's runs cannot become a baseline, or "" when they can."""
    if not outcomes or any(o.attempt_dir is None for o in outcomes):
        return "no attempt to bank"
    bad = [o.status for o in outcomes if o.status not in BANKABLE]
    if bad:
        return f"status {', '.join(sorted(set(bad)))}"
    # The decision projection, not the plan bytes: a plan carries per-run noise that
    # no seed controls, so comparing bytes would refuse every bank there is.
    if len({o.decision_sha256 for o in outcomes}) > 1:
        return "the seeds disagreed on the decision"
    return ""


def write_reference(path: Path, reference: dict) -> Path:
    """Rewrite the private reference, keeping a timestamped copy of what it replaced."""
    backup = path.with_name(f"{path.name}.backup-{time.strftime('%Y%m%dT%H%M%S')}")
    backup.write_bytes(path.read_bytes())
    backup.chmod(0o600)
    path.write_text(json.dumps(reference, indent=2, ensure_ascii=False) + "\n")
    path.chmod(0o600)
    return backup


def bank_routes(path: Path, reference: dict, banked: dict) -> None:
    """Replace each named route's HEAD baseline with what this session just replayed."""
    for key, baseline in banked.items():
        entry = reference.get("routes", reference)[key]
        entry["head_baseline"] = baseline
        # A route that once could not produce a baseline now has one; leaving the old
        # failure record beside it would read as though it still cannot.
        entry.pop("head_baseline_failed", None)
    backup = write_reference(path, reference)
    print(f"banked {', '.join(sorted(banked))}; previous reference kept at {backup}")


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
    parser.add_argument(
        "--bank",
        action="store_true",
        help="replace each replayed route's HEAD baseline with what this session produced",
    )
    args = parser.parse_args()

    reference_path = args.reference.expanduser()
    reference = json.loads(reference_path.read_text())
    routes = reference_routes(reference)
    wanted = [key for key in args.routes.split(",") if key] or list(routes)
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    outcomes: list[RouteOutcome] = []
    banked: dict[str, dict] = {}
    for key in wanted:
        route_outcomes = [
            run_route(
                key, routes[key], seed=seed, python=args.python, out=args.out, timeout=args.timeout
            )
            for seed in args.seeds.split(",")
        ]
        for outcome in route_outcomes:
            print(
                f"{outcome.route:14s} seed={outcome.seed:3s} {outcome.status:18s} {outcome.seconds:7.1f}s blocked={outcome.blocked_calls} bank+={outcome.bank_rows_added} carriers={outcome.carriers} {outcome.detail}"
            )
        outcomes.extend(route_outcomes)
        if args.bank:
            refusal = bank_refusal(route_outcomes)
            if refusal:
                print(f"{key:14s} not banked: {refusal}")
            else:
                banked[key] = banked_baseline(routes[key], route_outcomes[-1])
    if banked:
        bank_routes(reference_path, reference, banked)
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
