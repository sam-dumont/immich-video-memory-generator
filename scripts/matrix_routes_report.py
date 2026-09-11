"""Table a matrix-routes manifest, and say how each route moved against its reference.

Two tables. The first is what came out: carriers, content seconds, whether the
duration landed, what the model cost, how long the film is, and whether Immich
took it. The second is the only one that decides anything — a route whose plan
bytes match the accepted one is `identical` and keeps the owner's grade; anything
else has to be watched again, which is what the third verdict says out loud.

The console form carries counts only. Asset ids, attempt paths and film paths go
to the private report file beside the manifest.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

IDENTICAL = "identical"
SAME_CARRIERS = "same-carriers"
CHANGED = "changed; owner approval not transferred"
NO_REFERENCE = "no reference"
NOT_COLLECTED = "not collected"


def reference_plan(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What the accepted run selected, preferring its own plan file over the summary.

    The overlay may carry only the carrier ids. When it also names the attempt
    directory, the plan there is richer — it knows which of those carriers were
    favourites, which is the retention number that matters.
    """
    record = {
        "plan_sha256": entry.get("plan_sha256"),
        "carrier_asset_ids": [str(x) for x in entry.get("carrier_asset_ids") or ()],
        "favourite_asset_ids": None,
        "content_seconds": None,
    }
    attempt = entry.get("attempt_path")
    plan_path = Path(attempt).expanduser() / "plan.private.json" if attempt else None
    if plan_path is None or not plan_path.exists():
        return record
    plan = json.loads(plan_path.read_text())
    carriers = plan.get("carriers") or []
    record["carrier_asset_ids"] = [str(c.get("asset_id")) for c in carriers]
    record["favourite_asset_ids"] = [str(c["asset_id"]) for c in carriers if c.get("favourite")]
    record["content_seconds"] = plan.get("content_seconds")
    return record


def _delta(new: Any, old: Any) -> float | None:
    if new is None or old is None:
        return None
    return round(float(new) - float(old), 2)


def compare(case: Mapping[str, Any], entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """One route's verdict against the accepted run, plus what moved."""
    plan = case.get("plan") or {}
    fresh_ids = plan.get("carrier_asset_ids")
    if fresh_ids is None:
        return {"verdict": NOT_COLLECTED}
    if not entry:
        return {"verdict": NO_REFERENCE}
    reference = reference_plan(entry)
    old_ids = reference["carrier_asset_ids"]
    if plan.get("plan_sha256") and plan["plan_sha256"] == reference["plan_sha256"]:
        verdict = IDENTICAL
    elif fresh_ids == old_ids:
        verdict = SAME_CARRIERS
    else:
        verdict = CHANGED
    favourites = reference["favourite_asset_ids"]
    kept = None if favourites is None else len(set(favourites) & set(fresh_ids))
    return {
        "verdict": verdict,
        "added": len(set(fresh_ids) - set(old_ids)),
        "removed": len(set(old_ids) - set(fresh_ids)),
        "content_delta_seconds": _delta(plan.get("content_seconds"), reference["content_seconds"]),
        "reference_favourites": None if favourites is None else len(favourites),
        "favourites_kept": kept,
    }


def report_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    references = manifest.get("reference") or {}
    rows = []
    for case in manifest["cases"]:
        plan = case.get("plan") or {}
        film = case.get("film") or {}
        metrics = plan.get("llm_metrics") or {}
        realization = plan.get("duration_realization") or {}
        rows.append(
            {
                "route": case["id"],
                "status": case["status"],
                "carriers": len(plan.get("carrier_asset_ids") or ()) or None,
                "content_seconds": plan.get("content_seconds"),
                "realization": realization.get("status"),
                "llm_calls": metrics.get("llm_calls"),
                "llm_cache_hits": metrics.get("llm_cache_hits"),
                "film_seconds": film.get("seconds"),
                "film_path": film.get("path"),
                "attempt_path": plan.get("attempt_path"),
                "immich_asset_id": (case.get("delivery") or {}).get("immich_asset_id"),
                "error": case.get("error"),
                **compare(case, references.get(case["id"])),
            }
        )
    return rows


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def _table(headers: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    widths = [max(len(headers[i]), *(len(row[i]) for row in body)) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
    return "\n".join(lines)


_OUTCOME_HEADERS = ("route", "status", "carriers", "content s", "duration", "calls/hits", "film s")
_MOVED_HEADERS = ("route", "verdict", "+carriers", "-carriers", "content d", "favourites kept")


def _outcome_row(row: Mapping[str, Any], uploaded: str) -> list[str]:
    return [
        row["route"],
        row["status"],
        _cell(row["carriers"]),
        _cell(row["content_seconds"]),
        _cell(row["realization"]),
        f"{_cell(row['llm_calls'])}/{_cell(row['llm_cache_hits'])}",
        _cell(row["film_seconds"]),
        uploaded,
    ]


def _moved_row(row: Mapping[str, Any]) -> list[str]:
    total = row.get("reference_favourites")
    kept = row.get("favourites_kept")
    return [
        row["route"],
        row["verdict"],
        _cell(row.get("added")),
        _cell(row.get("removed")),
        _cell(row.get("content_delta_seconds")),
        "—" if total is None else f"{kept}/{total}",
    ]


def console_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The owner's terminal view: counts only, never an asset id."""
    outcome = _table(
        (*_OUTCOME_HEADERS, "in immich"),
        [_outcome_row(row, "yes" if row["immich_asset_id"] else "no") for row in rows],
    )
    moved = _table(_MOVED_HEADERS, [_moved_row(row) for row in rows])
    return f"{outcome}\n\n{moved}"


def markdown_report(manifest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    """The private form: the same two tables, with the identities the owner needs."""
    outcome = _table(
        (*_OUTCOME_HEADERS, "immich asset id"),
        [_outcome_row(row, _cell(row["immich_asset_id"])) for row in rows],
    )
    moved = _table(_MOVED_HEADERS, [_moved_row(row) for row in rows])
    failures = [f"- {row['route']}: {row['error']}" for row in rows if row["error"]]
    parts = [
        f"# Matrix routes — {manifest.get('built_at', 'unbuilt')}",
        f"Album: {manifest.get('album_name') or '(no upload)'}",
        "## What came out",
        f"```\n{outcome}\n```",
        "## What moved against the reference",
        f"```\n{moved}\n```",
    ]
    if failures:
        parts += ["## Failures", "\n".join(failures)]
    parts.append("## Artifacts")
    parts += [
        f"- {row['route']}: film {_cell(row['film_path'])} · attempt {_cell(row['attempt_path'])}"
        for row in rows
    ]
    return "\n\n".join(parts) + "\n"
