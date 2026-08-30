#!/usr/bin/env python3
"""Stage B0 -- does a hosted teacher beat the pinned local 27B, on the same images?

Runs no model itself. It reads two labels files produced by ``teacher_label.py``
with different ``--provider``/``--label-suffix``, writes a side-by-side file a
human can read, and scores field-level agreement with the SAME arithmetic the
§7 gates use, so the number means the same thing here as it does there.

Teacher choice is the owner's call. This script produces the numbers and stops.

    uv run --with pyarrow scripts/distill/gap_probe.py --split validation \\
        --b melious
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distill_common import DEFAULT_ROOT, read_parquet  # noqa: E402
from eval_gates import TEACHER_SELF_AGREEMENT, normalised_ted, score_fields  # noqa: E402

# §7 gate 1's ceiling doubles as the noise floor for teacher-vs-teacher work:
# two teachers agreeing at or above it are indistinguishable on this evidence.
NOISE_FLOOR = TEACHER_SELF_AGREEMENT


def tag_suffix(tag: str) -> str:
    """"melious" -> "-melious"; "" -> "" (the pinned local run)."""
    tag = tag.strip().lstrip("-")
    return f"-{tag}" if tag else ""


def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_parquet(path)
    return {
        str(row["image_id"]): row
        for row in rows
        if row.get("status") == "ok" and row.get("text")
    }


def fields_of(row: dict[str, Any]) -> dict[str, str]:
    """Compare the scrubbed, banked cells -- what actually reaches the student."""
    out = {"description": str(row.get("text") or "")}
    setting = str(row.get("setting") or "")
    if setting:
        out["setting"] = setting
    return out


def summarise(rows: dict[str, dict], label: str) -> dict[str, Any]:
    latencies = [float(r.get("latency_s") or 0) for r in rows.values()]
    lengths = [len(str(r.get("text") or "")) for r in rows.values()]
    return {
        "label": label,
        "model": next((str(r.get("model")) for r in rows.values()), "?"),
        "n": len(rows),
        "median_latency_s": round(statistics.median(latencies or [0]), 2),
        "median_chars": int(statistics.median(lengths or [0])),
        "median_redactions": int(statistics.median(
            [int(r.get("redactions") or 0) for r in rows.values()] or [0]
        )),
    }


def build_report(args: argparse.Namespace) -> tuple[str, list[dict], dict[str, Any]]:
    split_dir = args.root / args.split
    a_rows = load_labels(split_dir / f"labels{tag_suffix(args.a)}.parquet")
    b_rows = load_labels(split_dir / f"labels{tag_suffix(args.b)}.parquet")
    shared = sorted(set(a_rows) & set(b_rows))
    if not shared:
        raise SystemExit("the two label files share no successfully-labelled image")
    truth = {one: fields_of(a_rows[one]) for one in shared}
    other = {one: fields_of(b_rows[one]) for one in shared}
    tally, _ = score_fields(truth, other, mode="token", threshold=args.token_threshold)
    stats = {
        "shared": len(shared),
        "agreement_f1": round(tally.micro_f1, 4),
        "nted": round(normalised_ted(truth, other), 4),
        "a": summarise(a_rows, args.a or "local"),
        "b": summarise(b_rows, args.b or "hosted"),
    }
    side_by_side = [
        {
            "image_id": one,
            "a_model": a_rows[one].get("model"),
            "a_text": a_rows[one].get("text"),
            "a_setting": a_rows[one].get("setting"),
            "b_model": b_rows[one].get("model"),
            "b_text": b_rows[one].get("text"),
            "b_setting": b_rows[one].get("setting"),
        }
        for one in shared
    ]
    verdict = (
        "INDISTINGUISHABLE -- keep the pinned local 27B"
        if tally.micro_f1 >= NOISE_FLOOR
        else "DIFFERENT -- the two teachers disagree beyond the noise floor; owner decides"
    )
    report = f"""# Stage B0 -- teacher gap probe

Images compared: **{len(shared)}** (labelled successfully by both).

| | A ({stats["a"]["label"]}) | B ({stats["b"]["label"]}) |
|---|---|---|
| model | `{stats["a"]["model"]}` | `{stats["b"]["model"]}` |
| labelled ok | {stats["a"]["n"]} | {stats["b"]["n"]} |
| median latency | {stats["a"]["median_latency_s"]}s | {stats["b"]["median_latency_s"]}s |
| median chars | {stats["a"]["median_chars"]} | {stats["b"]["median_chars"]} |
| median redactions | {stats["a"]["median_redactions"]} | {stats["b"]["median_redactions"]} |

**Field agreement (micro-F1, A as reference): {stats["agreement_f1"]}**
nTED {stats["nted"]}  ·  noise floor {NOISE_FLOOR}

**{verdict}**

Agreement is not quality: it says how alike the two teachers are, not which is
right. Read `gap_probe.jsonl` and judge the disagreements by eye before choosing.

## Decision rule

The local 27B stays unless the hosted teacher beats it **beyond the ~95% noise
floor** -- agreement at or above {NOISE_FLOOR} means the two are
indistinguishable on this evidence and the free local one wins by default.

A hosted teacher also wins **ties on speed**, if the owner opts to pay: at
~1.2 s/call a hosted endpoint labels 25k images in **~8 h**, against roughly two
nights on the local 27B. That is a cost-and-calendar decision, not a quality one,
and it is the owner's to make.
"""
    return report, side_by_side, stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--split", default="validation")
    # Bare tags, not literal suffixes: argparse would treat "-melious" as a flag.
    parser.add_argument("--a", default="", help="tag of teacher A (default: empty = the pinned run)")
    parser.add_argument("--b", required=True, help="tag of teacher B, e.g. --b melious")
    parser.add_argument("--token-threshold", type=float, default=0.5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report, side_by_side, stats = build_report(args)
    out_dir = args.root / args.split
    (out_dir / "gap_probe.md").write_text(report, encoding="utf-8")
    with (out_dir / "gap_probe.jsonl").open("w", encoding="utf-8") as handle:
        for row in side_by_side:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(report)
    print(f"side-by-side: {len(side_by_side)} rows -> {out_dir / 'gap_probe.jsonl'}")
    print("\nSTOP: teacher choice is the owner's call. Report these numbers.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
