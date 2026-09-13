"""The two files a setup-matrix run publishes: the record and the table.

`summary.data.json` is the record other pages cite, shaped like the research data
files under `docs/research/`: a schema name, the scope, the conditions the numbers
were taken under, the rows, and an `unmeasured` list that says out loud what this
run could not measure. `summary.md` is the one table a person reads.

Nothing here computes a number the capture did not observe. A null stays null and
earns a line in `unmeasured`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "setup-matrix-v1"

# One cell's own record, written beside its logs the moment that cell finishes.
CELL_RECORD = "timing.json"

# Everything is compared against the Mac running the local model: it is the
# setup the editorial passes were written and graded against.
REFERENCE_CELL = "mac-local"

CONDITIONS = (
    "One memory per cell: the same month of the same library, the same target length.",
    "Single observations. This is a comparison of setups, not a quality study.",
    "Preparation cold is the first prepare of the scope on that host, warm the second"
    " immediately after. A host whose bank already held the scope is named in unmeasured.",
    "Selection and render seconds are the run's own measured-this-run block, not a wall clock"
    " around the process.",
    "Token counts come from the end-of-run summary, which rounds at or above 1000.",
)


def jaccard(left: list[str], right: list[str]) -> float | None:
    """Overlap of two cuts as a fraction, or None when either cut is empty."""
    first, second = set(left), set(right)
    if not first or not second:
        return None
    return round(len(first & second) / len(first | second), 3)


def order_kept(reference: list[str], other: list[str]) -> bool | None:
    """Whether the shots both cuts kept still play in the same relative order.

    Chronological order is a hard rule, so this should be true everywhere. It is
    recorded because a false here means a setup changed the order of the story,
    which is a bigger finding than any timing in the table.
    """
    shared = [asset for asset in other if asset in set(reference)]
    if len(shared) < 2:
        return None
    ranking = {asset: index for index, asset in enumerate(reference)}
    return all(ranking[a] < ranking[b] for a, b in zip(shared, shared[1:], strict=False))


def _timing_gaps(row: dict) -> list[str]:
    timing = row.get("timing") or {}
    missing = sorted(name for name, value in timing.items() if value is None)
    if not missing:
        return []
    return [f"{row['id']}: {', '.join(missing)}. The run did not report it."]


def _usage_gaps(row: dict) -> list[str]:
    usage = row.get("hosted_usage") or {}
    if not row.get("hosted"):
        return []
    gaps = []
    if usage.get("est_cost_eur") is None:
        gaps.append(
            f"{row['id']}: API cost in euro. The provider returns no price with a completion,"
            " so a figure here would be a price list, not a measurement."
        )
    if usage.get("counted_exactly") is False:
        gaps.append(f"{row['id']}: exact token counts. The run summary rounds at or above 1000.")
    return gaps


def write_cell_record(out_dir: Path, row: dict) -> None:
    """Leave a cell's record beside its own logs, where a later invocation can find it."""
    path = out_dir / row["id"] / CELL_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2) + "\n")


def read_cell_records(out_dir: Path) -> list[dict]:
    """Every cell that has landed in this output directory, whichever run put it there.

    A lane is one invocation, so the rows any single invocation holds are its own
    lane and nothing else. What is on disk is the run.
    """
    return [json.loads(path.read_text()) for path in sorted(out_dir.glob(f"*/{CELL_RECORD}"))]


def build_summary(
    *,
    library: str,
    month: str,
    image: str,
    rows: list[dict],
    inference_warmup_s: float | None = None,
) -> dict:
    """The record, with every row's overlap against the reference cut worked out."""
    reference = next(
        (row.get("selected_asset_ids") or [] for row in rows if row["id"] == REFERENCE_CELL),
        [],
    )
    unmeasured: list[str] = []
    for row in rows:
        selected = row.get("selected_asset_ids") or []
        row["overlap_vs_cell_1"] = (
            1.0 if row["id"] == REFERENCE_CELL and selected else jaccard(reference, selected)
        )
        row["order_kept"] = (
            True if row["id"] == REFERENCE_CELL and selected else order_kept(reference, selected)
        )
        if row.get("skip_reason"):
            unmeasured.append(f"{row['id']}: not run. {row['skip_reason']}")
            continue
        unmeasured.extend(_timing_gaps(row))
        unmeasured.extend(_usage_gaps(row))
        if row.get("prepare_cache_primed"):
            unmeasured.append(
                f"{row['id']}: a true cold preparation. The bank already held this scope,"
                " so prepare_cold_s is a re-read, not a first derivation."
            )
    if not reference:
        unmeasured.append(
            f"Overlap against {REFERENCE_CELL}: the reference cell produced no cut in this run."
        )
    return {
        "schema": SCHEMA,
        "library": library,
        "month": month,
        "image": image,
        # How long the inference service took to answer its first real request,
        # which is a cost of the setup and not of the cell that would have paid it.
        "inference_warmup_s": inference_warmup_s,
        "reference_cell": REFERENCE_CELL,
        "scope": f"One monthly memory over {month}, run once per setup.",
        "conditions": list(CONDITIONS),
        "cells": rows,
        "unmeasured": unmeasured,
    }


_HEADERS = (
    "cell",
    "tier",
    "reader",
    "facts",
    "prep cold",
    "prep warm",
    "selection",
    "render",
    "peak RSS",
    "cost",
    "#kept",
    "overlap",
    "film",
)


def _seconds(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0f}s"


def _megabytes(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0f} MB"


def _fraction(value: Any) -> str:
    return "-" if value is None else f"{float(value):.0%}"


def _row_cells(row: dict) -> list[str]:
    timing = row.get("timing") or {}
    usage = row.get("hosted_usage") or {}
    video = row.get("video") or {}
    if row.get("skip_reason"):
        return [row["id"], row["tier"], row["reader"], row["facts"], *(["skipped"] * 9)]
    return [
        row["id"],
        row["tier"],
        row["reader"],
        row["facts"],
        _seconds(timing.get("prepare_cold_s")),
        _seconds(timing.get("prepare_warm_s")),
        _seconds(timing.get("selection_s")),
        _seconds(timing.get("render_s")),
        _megabytes(timing.get("peak_rss_mb")),
        "-" if usage.get("est_cost_eur") is None else f"EUR {usage['est_cost_eur']:.3f}",
        str(len(row.get("selected_asset_ids") or [])),
        _fraction(row.get("overlap_vs_cell_1")),
        _seconds(video.get("duration_s")),
    ]


def build_markdown(summary: dict) -> str:
    """The table, plus the list of what this run did not measure."""
    lines = [
        f"# Setup matrix: {summary['library']}, {summary['month']}",
        "",
        f"One memory per setup, {summary['month']}, image `{summary['image']}`."
        f" Overlap is against `{summary['reference_cell']}`.",
        "",
        "| " + " | ".join(_HEADERS) + " |",
        "|" + "|".join(["---"] * len(_HEADERS)) + "|",
    ]
    lines += ["| " + " | ".join(_row_cells(row)) + " |" for row in summary["cells"]]
    order_breaks = [row["id"] for row in summary["cells"] if row.get("order_kept") is False]
    if order_breaks:
        lines += [
            "",
            "## The order changed",
            "",
            "Chronological order is a hard rule, so these setups are a defect, not a result:",
            "",
            *[f"- {name}" for name in order_breaks],
        ]
    lines += ["", "## Not measured", ""]
    lines += [f"- {item}" for item in summary["unmeasured"]] or ["- Nothing."]
    return "\n".join(lines) + "\n"
