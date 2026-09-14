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
    "Every cell banks in a cache of its own, so nothing one cell derived or decided"
    " reaches the next. Model files are the exception and stay shared.",
    "The models column is what `models fetch` cost that cell's container, on its own"
    " and never inside preparation: a remote lane starts with an empty models volume,"
    " and the cells that fetch nothing say so in unmeasured.",
    "Preparation cold is the first prepare of the scope into this cell's own cache,"
    " warm the second immediately after. A cell re-run over its own cache is named"
    " in unmeasured.",
    "Selection and render seconds are the run's own measured-this-run block, not a wall clock"
    " around the process.",
    "Token counts come from the end-of-run summary, which rounds at or above 1000.",
    "The render device column is two answers, because they are two pieces of silicon:"
    " what drew the title screens and what encoded the film. Both are read off lines"
    " the run printed, and a cell that printed neither shows a dash rather than a"
    " guess from its lane.",
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
    """One line per missing timing, and its own reason where the cell recorded one."""
    timing = row.get("timing") or {}
    notes = row.get("measurement_notes") or {}
    missing = sorted(name for name, value in timing.items() if value is None)
    gaps = [f"{row['id']}: {notes[name]}" for name in missing if name in notes]
    unexplained = [name for name in missing if name not in notes]
    if unexplained:
        gaps.append(f"{row['id']}: {', '.join(unexplained)}. The run did not report it.")
    return gaps


def _artifact_gaps(row: dict) -> list[str]:
    """Notes about something that is not a timing, which `_timing_gaps` never reaches.

    A cell whose copy-out failed has its numbers off its own stdout and no film,
    and the run says which of the two it is looking at.
    """
    timing = row.get("timing") or {}
    notes = row.get("measurement_notes") or {}
    return [f"{row['id']}: {note}" for name, note in notes.items() if name not in timing]


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
    inference_gpu_product: str | None = None,
    captioner_warmup_s: float | None = None,
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
        unmeasured.extend(_artifact_gaps(row))
        unmeasured.extend(_usage_gaps(row))
        if row.get("prepare_cache_primed"):
            unmeasured.append(
                f"{row['id']}: a true cold preparation. This cell's own cache was already"
                " there, so prepare_cold_s is a re-read, not a first derivation."
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
        # The overlays pin a release of their own, so the service a cell called is
        # not the app image unless the run said so. Taken from the cells that used it.
        "inference_image": next(
            (row["inference_image"] for row in rows if row.get("inference_image")), None
        ),
        # How long the inference service took to answer its first real request,
        # which is a cost of the setup and not of the cell that would have paid it.
        "inference_warmup_s": inference_warmup_s,
        # Which card answered the facts requests. Read off the node the service
        # pod landed on, because unless the run pinned one with
        # `--inference-node-product` the scheduler picks, and a table comparing
        # two cards has to say which one was doing the classifying underneath.
        "inference_gpu_product": inference_gpu_product,
        # The same figure for the caption server the full-tier cluster cells
        # call: weights onto a cold claim plus the first completion, paid once
        # by the run rather than by whichever cell happened to go first.
        "captioner_warmup_s": captioner_warmup_s,
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
    "models",
    "prep cold",
    "prep warm",
    "selection",
    "render",
    "render device",
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


def render_device(row: dict) -> str:
    """What drew the titles and what encoded the film, as one column.

    Two answers, because they are two pieces of silicon and they disagreed on the
    first cluster run: `CUDA titles / software` is a Job that got a shared card
    for the title kernels and no NVENC to encode with, and it is why those rows
    are slower than the card suggests.
    """
    titles, encoder = row.get("title_backend"), row.get("encoder")
    if not titles and not encoder:
        return "-"
    return f"{titles or '?'} titles / {encoder or '?'}"


def _row_cells(row: dict) -> list[str]:
    timing = row.get("timing") or {}
    usage = row.get("hosted_usage") or {}
    video = row.get("video") or {}
    if row.get("skip_reason"):
        columns = len(_HEADERS) - 4
        return [row["id"], row["tier"], row["reader"], row["facts"], *(["skipped"] * columns)]
    return [
        row["id"],
        row["tier"],
        row["reader"],
        row["facts"],
        _seconds(timing.get("models_fetch_s")),
        _seconds(timing.get("prepare_cold_s")),
        _seconds(timing.get("prepare_warm_s")),
        _seconds(timing.get("selection_s")),
        _seconds(timing.get("render_s")),
        render_device(row),
        _megabytes(timing.get("peak_rss_mb")),
        "-" if usage.get("est_cost_eur") is None else f"EUR {usage['est_cost_eur']:.3f}",
        str(len(row.get("selected_asset_ids") or [])),
        _fraction(row.get("overlap_vs_cell_1")),
        _seconds(video.get("duration_s")),
    ]


# Which card, which the column cannot say: it names the silicon path and two
# cards share one. This is the half that answers "T1000 or 1070".
_CARD_LEAD = (
    "These cells asked the cluster for one named card and pinned"
    " `hardware.backend: nvidia`, so the render column above is NVENC on:"
)


def _render_devices(rows: list[dict]) -> list[str]:
    """Which cells rendered on a GPU, and on which card."""
    named = [row for row in rows if row.get("gpu_product")]
    if not named:
        return []
    return [
        "",
        "## Render device",
        "",
        _CARD_LEAD,
        "",
        *[f"- `{row['id']}`: {row['gpu_product']} ({render_device(row)})" for row in named],
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
    lines += _render_devices(summary["cells"])
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
