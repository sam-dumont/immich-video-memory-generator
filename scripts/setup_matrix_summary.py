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
    "The cost column is list price times measured tokens: the price list in the manifest, at"
    " the date each row of it names, multiplied by the tokens the run reported. It is an"
    " estimate and it carries that rounding with it. Nothing here asks a provider what a"
    " completion cost, because none of them answers, and nothing here converts between"
    " currencies: each figure is in the one its shop publishes in.",
    "A seeded cell prepared nothing. Preparation is a fact about the host, the tier and"
    " where the picture facts come from, so cells that vary only the reader take the"
    " named cell's bank with every model answer removed from it, and its prepare"
    " columns point at the cell that measured them.",
    "The contract column is rejections/repairs: how many answers a reading contract refused,"
    " and how many of those the run asked again with the rejection spelled out. A rules cell"
    " never asked a model anything and shows a dash.",
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
    if usage.get("est_cost") is None:
        gaps.append(
            f"{row['id']}: what the API cost. The provider returns no price with a completion and"
            " the manifest holds no list price for this reader and model, so there is nothing to"
            " multiply the tokens by."
        )
    if usage.get("counted_exactly") is False:
        gaps.append(f"{row['id']}: exact token counts. The run summary rounds at or above 1000.")
    return gaps


def _contract_gaps(row: dict) -> list[str]:
    """A reader that called a model and left no transcripts to count."""
    if row.get("reader") == "rules":
        return []
    if (row.get("contract") or {}).get("rejections") is None:
        return [
            f"{row['id']}: contract health. This cell kept no call transcripts, so how often a"
            " reading contract refused its reader was never counted."
        ]
    return []


# The key a shop's own currency sits under, beside its models.
CURRENCY = "currency"


def estimated_cost(usage: dict, price: dict | None) -> float | None:
    """List price times measured tokens, or None when either half is missing.

    Not a bill. No provider in the matrix returns a price with a completion, so
    this is the published price list multiplied by what the run counted, and it
    carries the run summary's own rounding with it.
    """
    tokens_in, tokens_out = usage.get("tokens_in"), usage.get("tokens_out")
    if not price or tokens_in is None or tokens_out is None:
        return None
    spent = tokens_in * float(price["input_per_million"])
    spent += tokens_out * float(price["output_per_million"])
    return round(spent / 1_000_000, 4)


def _apply_price(row: dict, pricing: dict) -> None:
    """Put a figure on a row that measured tokens and has a price to multiply them by.

    Keyed by reader as well as model id: `glm-5.3-flash` is sold by two shops at
    two prices, and only one of them has a page in the manifest. The currency is
    the shop's own and is never converted, so two rows bought in two currencies
    stay two figures rather than becoming one made up out of a rate.
    """
    usage = row.get("hosted_usage") or {}
    shop = pricing.get(row.get("reader") or "") or {}
    price = shop.get(row.get("reader_model") or "")
    cost = estimated_cost(usage, price if isinstance(price, dict) else None)
    if cost is None:
        return
    usage["est_cost"] = cost
    usage["cost_currency"] = shop.get(CURRENCY)
    usage["price_source"] = price.get("source")
    usage["price_retrieved"] = price.get("retrieved")


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
    pricing: dict | None = None,
    captioner_warmup_s: float | None = None,
    captioner_device: str | None = None,
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
        _apply_price(row, pricing or {})
        unmeasured.extend(_timing_gaps(row))
        unmeasured.extend(_artifact_gaps(row))
        unmeasured.extend(_usage_gaps(row))
        unmeasured.extend(_contract_gaps(row))
        # `is True` on purpose: a seeded cell puts a sentence in this field, and
        # its preparation is not a re-read of its own last run, it is another
        # cell's measurement, named under `unmeasured` by that cell's own reason.
        if row.get("prepare_cache_primed") is True:
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
        # Which device wrote the captions, because a full-tier row is mostly
        # preparation and preparation is mostly captions: 3.5 s a picture on the
        # CPU image against tenths of a second on a card. Null when no cell in
        # this run asked for a caption server.
        "captioner_device": captioner_device,
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
    "contract",
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


def _prepared(row: dict, value: Any) -> str:
    """A preparation second, or the cell that measured it for this one."""
    return f"= {row['seeded_from']}" if row.get("seeded_from") else _seconds(value)


def _money(usage: dict) -> str:
    """An estimate with the currency it was priced in, which is never converted."""
    cost = usage.get("est_cost")
    return "-" if cost is None else f"{usage.get('cost_currency') or '?'} {cost:.3f}"


def _contract(row: dict) -> str:
    """`rejections/repairs`, or a dash for a cell that asked no model anything."""
    contract = row.get("contract") or {}
    if contract.get("rejections") is None:
        return "-"
    return f"{contract['rejections']}/{contract.get('repairs') or 0}"


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
        _prepared(row, timing.get("prepare_cold_s")),
        _prepared(row, timing.get("prepare_warm_s")),
        _seconds(timing.get("selection_s")),
        _seconds(timing.get("render_s")),
        render_device(row),
        _megabytes(timing.get("peak_rss_mb")),
        _money(usage),
        str(len(row.get("selected_asset_ids") or [])),
        _fraction(row.get("overlap_vs_cell_1")),
        _contract(row),
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


# What the cost column is, said where the cost column is. Anybody reading a euro
# figure off a table assumes somebody was billed it, and nobody was.
_COST_LEAD = (
    "The cost column is list price times measured tokens: the price list in"
    " `scripts/setup_matrix.yaml`, at the date each row of it names, multiplied by the token"
    " counts the run printed. It is an estimate of what the run would cost at list, the counts"
    " it multiplies are rounded at or above 1000, and each figure is in the currency its shop"
    " publishes in with no rate applied between them. What each row is made of:"
)


def _thousands(count: Any) -> str:
    return "?" if count is None else f"{int(count) / 1000:.1f}k" if count >= 1000 else str(count)


def _cost_line(row: dict) -> str:
    """One row's bill, and the arithmetic behind it, so nobody has to take it on faith."""
    usage = row["hosted_usage"]
    return (
        f"- `{row['id']}` {usage.get('cost_currency') or '?'} {usage['est_cost']:.4f}"
        f" for `{row['reader_model']}`:"
        f" {usage.get('calls') or 0} calls, {_thousands(usage.get('tokens_in'))} in /"
        f" {_thousands(usage.get('tokens_out'))} out, {usage.get('images_sent') or 0} tiles,"
        f" at {usage['price_source']}"
        f" ({usage['price_retrieved']})"
    )


def _cost_basis(rows: list[dict]) -> list[str]:
    """Which rows carry a euro figure, what it was made of, and which published price."""
    priced = [
        row
        for row in rows
        if (row.get("hosted_usage") or {}).get("est_cost") is not None
        and row["hosted_usage"].get("price_source")
    ]
    if not priced:
        return []
    return ["", "## Cost", "", _COST_LEAD, "", *[_cost_line(row) for row in priced]]


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
    lines += _cost_basis(summary["cells"])
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
