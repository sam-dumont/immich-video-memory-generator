"""Does asking the audience question of twelve carriers at once keep every hold?

Replays a finished run's single audience requests (the `calls/` directory of an editorial
attempt) through the batched format against a live reader, and asks each carrier's single
question again under the current prompt so both sides are read by the same reader today. It
reports, per carrier, the holds the batch kept, lost and added against the single question. A
hold is any finding other than "none", before the supporting-facts check, and again as the
production verdict after it.

Two batched modes are reported: the source order alone, and production's two orders (rows as
they come and shuffled; a carrier either order holds is held). Each is compared with the single
question asked again now and with the run's recorded answers. The owner's rule:
`thin_batched_audience` is switched on only when the two-order mode loses 0 holds against both.

Not part of CI: it needs a live reader and a run's private call records. It refuses to start
while another GPU job runs (`immich-memories ... generate`, `mlx_train`) unless `--force`.

    uv run python scripts/probe_audience_batch.py \\
        --calls <attempt>/calls --config <run>/config.yaml --out <scratch>/audience-batch.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: S404 - pgrep only, fixed arguments
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from immich_memories.analysis.editorial_audience_batch import (
    AUDIENCE_BATCH_SIZE,
    ORDERS,
    _ordered,
    batched_activity_prompt,
    read_batched_activity,
)
from immich_memories.analysis.editorial_shareability_audience import (
    _first_json_object,
    audience_check_prompt,
    parse_audience_verdict,
)

_REQUEST = re.compile(r"^\d+-shareability-\d+-activity\.request\.private\.txt$")
_NUDITY_LINE = "- nudity_shirtless_or_underwear:"
_BUSY = ("bin/immich-memories .*generate", "mlx_train")


def main() -> int:
    args = _arguments()
    if not args.force and (busy := _gpu_busy()):
        print(f"Another GPU job is running ({busy}); run this later or pass --force.")
        return 2
    reader = Reader(args.config)
    carriers = _recorded_carriers(args.calls)
    if args.limit:
        carriers = carriers[: args.limit]
    print(f"{len(carriers)} recorded audience requests; model {reader.model}")
    started = time.monotonic()
    single = {c["id"]: _finding(reader.ask(c["single_prompt"], 120)) for c in carriers}
    single_seconds = time.monotonic() - started
    started = time.monotonic()
    batched, batch_calls = _batched(reader, carriers)
    batch_seconds = time.monotonic() - started
    report = _report(carriers, single, batched)
    report |= {
        "model": reader.model,
        "single_calls": len(carriers),
        "single_seconds": round(single_seconds, 1),
        "batch_calls": batch_calls,
        "batch_seconds": round(batch_seconds, 1),
        "batch_size": AUDIENCE_BATCH_SIZE,
    }
    args.out.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "carriers"}, indent=1))
    return 0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--calls", type=Path, required=True, help="an attempt's calls/ directory")
    parser.add_argument("--config", type=Path, required=True, help="the run's config.yaml")
    parser.add_argument("--out", type=Path, required=True, help="where the JSON report goes")
    parser.add_argument("--limit", type=int, default=0, help="only the first N requests")
    parser.add_argument("--force", action="store_true", help="run even while a GPU job runs")
    return parser.parse_args()


def _gpu_busy() -> str:
    for pattern in _BUSY:
        found = subprocess.run(  # noqa: S603 - fixed argv
            ["/usr/bin/pgrep", "-f", pattern], capture_output=True, text=True, check=False
        )
        if found.stdout.strip():
            return pattern
    return ""


class Reader:
    """The run's own reader, asked the way the editorial gateway asks it: temperature 0."""

    def __init__(self, config_path: Path) -> None:
        llm = (yaml.safe_load(config_path.read_text()) or {}).get("llm") or {}
        self.url = str(llm["base_url"]).rstrip("/") + "/chat/completions"
        self.model = str(llm["model"])
        self._key = str(llm.get("api_key") or "")
        self._extra = dict(llm.get("no_thinking_params") or {})

    def ask(self, prompt: str, max_tokens: int) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            **self._extra,
        }
        request = urllib.request.Request(  # noqa: S310 - the run's configured reader
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"},
        )
        with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
            payload = json.loads(response.read())
        return str(payload["choices"][0]["message"]["content"] or "")


def _recorded_carriers(calls: Path) -> list[dict[str, Any]]:
    """Each recorded single request's captions, the variant it was asked in, and its answer."""
    carriers = []
    for path in sorted(p for p in calls.iterdir() if _REQUEST.match(p.name)):
        prompt = path.read_text()
        captions = json.loads(prompt.rsplit("Captions:\n", 1)[1])
        members = [{"member": c["picture"], "caption": c["caption"]} for c in captions]
        allow_nudity = _NUDITY_LINE in prompt
        response = path.with_name(path.name.replace(".request.", ".response."))
        recorded = response.read_text() if response.exists() else ""
        evidence = {"members": members}
        carriers.append(
            {
                "id": path.name.split("-", 1)[0],
                "members": members,
                "allow_nudity": allow_nudity,
                "single_prompt": audience_check_prompt(evidence, allow_nudity=allow_nudity),
                "recorded": _finding(recorded),
            }
        )
    return carriers


def _batched(
    reader: Reader, carriers: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, str | None]], int]:
    """Each carrier's finding in each order of its batch, as production asks them."""
    answers: dict[str, dict[str, str | None]] = {c["id"]: dict.fromkeys(ORDERS) for c in carriers}
    calls = 0
    for allow in (True, False):
        group = [c for c in carriers if c["allow_nudity"] is allow]
        for start in range(0, len(group), AUDIENCE_BATCH_SIZE):
            chunk = {c["id"]: c for c in group[start : start + AUDIENCE_BATCH_SIZE]}
            for order in ORDERS:
                ids = _ordered(list(chunk), order)
                labels = [f"G{index + 1:02d}" for index in range(len(ids))]
                prompt = batched_activity_prompt(
                    [(label, chunk[i]["members"]) for label, i in zip(labels, ids, strict=True)],
                    allow_nudity=allow,
                )
                raw = reader.ask(prompt, 60 * len(ids) + 60)
                calls += 1
                try:
                    read = read_batched_activity(raw, labels, allow_nudity=allow)
                except ValueError:
                    read = {}
                for label, i in zip(labels, ids, strict=True):
                    answers[i][order] = _finding(read[label]) if label in read else None
    return answers, calls


def _either(by_order: dict[str, str | None]) -> str | None:
    """Production's two-order rule on findings: a hold in either order holds; `none` needs both."""
    held = [f for f in by_order.values() if f not in (None, "none")]
    if held:
        return held[0]
    return "none" if all(f == "none" for f in by_order.values()) else None


def _finding(raw: str) -> str | None:
    obj = _first_json_object(raw.strip()) if raw else None
    finding = obj.get("finding") if obj else None
    return finding if isinstance(finding, str) else None


def _verdict(finding: str | None, members: list[dict[str, str]]) -> str | None:
    if finding is None:
        return None
    parsed = parse_audience_verdict(
        json.dumps({"finding": finding, "why": "-"}), {"members": members}
    )
    return parsed[0] if parsed else None


def _report(carriers, single, batched) -> dict[str, Any]:
    rows = []
    for carrier in carriers:
        by_order = batched[carrier["id"]]
        rows.append(
            {
                "id": carrier["id"],
                "recorded": carrier["recorded"],
                "single": single[carrier["id"]],
                "one_order": by_order[ORDERS[0]],
                "two_orders": _either(by_order),
            }
        )
    for row, carrier in zip(rows, carriers, strict=True):
        for name in ("recorded", "single", "one_order", "two_orders"):
            row[f"{name}_verdict"] = _verdict(row[name], carrier["members"])
    finding_holds = lambda f: f not in (None, "none")  # noqa: E731
    verdict_holds = lambda v: v not in (None, "share")  # noqa: E731
    report: dict[str, Any] = {"carriers": rows}
    for mode in ("one_order", "two_orders"):
        for reference in ("single", "recorded"):
            report[f"{mode}_vs_{reference}"] = {
                "findings": _agreement(rows, reference, mode, finding_holds),
                "verdicts": _agreement(
                    rows, f"{reference}_verdict", f"{mode}_verdict", verdict_holds
                ),
            }
    report["single_vs_recorded"] = _agreement(rows, "recorded", "single", finding_holds)
    return report


def _agreement(rows, reference: str, other: str, holds) -> dict[str, int]:
    held = [row for row in rows if holds(row[reference])]
    return {
        "holds": len(held),
        "kept": sum(1 for row in held if holds(row[other])),
        # an unanswered batch carrier is asked alone in production, so it is not a lost hold
        "lost": sum(1 for row in held if row[other] is not None and not holds(row[other])),
        "unanswered": sum(1 for row in held if row[other] is None),
        "added": sum(1 for row in rows if not holds(row[reference]) and holds(row[other])),
        "same_finding": sum(1 for row in rows if row[reference] == row[other]),
    }


if __name__ == "__main__":
    sys.exit(main())
