#!/usr/bin/env python3
"""Run contact_sheet.py over a spec of memories and index the results.

Resumable: a sheet already on disk is skipped, so a sweep interrupted after
two hours picks up where it stopped.

    scripts/sweep_contact_sheets.py --spec my-sweep.json --out sheets

The spec is a JSON list of {"label": ..., "args": [...]} where args are passed
to `immich-memories generate`. Keep the spec outside the repo when it names
real people.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Photo scoring reports its own cache, once per pass, and a run has more than
# one: the shortlist pass and the look at whatever reached the cut uncached.
_PHOTO_CACHE = re.compile(r"Photo score cache: (\d+) hits, (\d+) misses")


def cache_activity(output: str) -> dict[str, int]:
    """What the caches did on a run, read back out of its log.

    The sweep only ever sees a subprocess's text, so this is the whole record
    of whether a month's minutes went on cache reads or on real work. Counting
    clips alone made a photo-era month — where the clip caches genuinely have
    nothing to do — indistinguishable from a month that did nothing at all.
    """
    photo = [(int(hits), int(misses)) for hits, misses in _PHOTO_CACHE.findall(output)]
    return {
        "cached_clips": output.count("Using cached analysis"),
        "analyzed_clips": output.count("Step 1a:"),
        "cached_photos": sum(hits for hits, _ in photo),
        "scored_photos": sum(misses for _, misses in photo),
    }


def was_cold(timing: dict) -> bool:
    """Did this run pay for its analysis, or read it back?

    Counts both media: a photo-era month analyses no clips either way, so on
    clips alone every one of them scored as warm. Reads the photo keys
    defensively — timings.json accumulates across sweeps and rows written
    before those keys existed come back on the next run.
    """
    fresh = timing["analyzed_clips"] + timing.get("scored_photos", 0)
    reused = timing["cached_clips"] + timing.get("cached_photos", 0)
    return fresh > reused


def summarise(elapsed: float, counts: dict[str, int]) -> str:
    """Where a run's time went, per medium.

    Both media are named even when one is at zero: an unlabelled pair of
    zeroes is what made a fully cached photo month look like a dead run.
    """
    return (
        f"{elapsed:.0f}s, "
        f"video: {counts['cached_clips']} cached / {counts['analyzed_clips']} analyzed, "
        f"photos: {counts['cached_photos']} cached / {counts['scored_photos']} scored"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=2400, help="Seconds per sheet")
    parser.add_argument("--redo", action="store_true", help="Re-render sheets that already exist")
    args = parser.parse_args()

    entries = json.loads(args.spec.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    logs = args.out / "logs"
    logs.mkdir(exist_ok=True)
    timings_path = args.out / "timings.json"
    timings = json.loads(timings_path.read_text()) if timings_path.exists() else []
    results = []

    for i, entry in enumerate(entries, 1):
        label = entry["label"]
        target = args.out / f"{label}.png"
        if target.exists() and not args.redo:
            print(f"[{i}/{len(entries)}] {label}: already rendered", flush=True)
            results.append((label, "cached", entry.get("note", "")))
            continue

        print(f"[{i}/{len(entries)}] {label}: running...", flush=True)
        started = time.monotonic()
        log_path = logs / f"{label}.log"
        command = [
            sys.executable,
            str(HERE / "contact_sheet.py"),
            "--label",
            label,
            "--out",
            str(args.out),
            "--",
            *entry["args"],
        ]
        output = ""
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
            output = (done.stdout or "") + (done.stderr or "")
            tail = output.strip().splitlines()
            status = tail[-1][:160] if tail else f"exit {done.returncode}"
        except subprocess.TimeoutExpired as expired:
            output = (expired.stdout or b"").decode(errors="replace") if expired.stdout else ""
            status = f"timed out after {args.timeout}s"
        elapsed = time.monotonic() - started
        log_path.write_text(output)

        # Where the time went. A month whose clips are already in the analysis
        # cache finishes in seconds; the same month cold pays for every frame.
        counts = cache_activity(output)
        print(f"    {status}  ({summarise(elapsed, counts)})", flush=True)
        timings.append(
            {
                "label": label,
                "seconds": round(elapsed, 1),
                **counts,
                "rendered": (args.out / f"{label}.png").exists(),
                "note": entry.get("note", ""),
            }
        )
        results.append((label, status, entry.get("note", "")))

    timings_path.write_text(json.dumps(timings, indent=2) + "\n")

    index = args.out / "index.md"
    lines = ["# Contact sheet sweep", ""]
    measured = [t for t in timings if t["seconds"]]
    if measured:
        cold = [t for t in measured if was_cold(t)]
        warm = [t for t in measured if not was_cold(t)]
        lines += ["## Timing", ""]
        for name, group in (("cold (mostly unanalyzed)", cold), ("warm (mostly cached)", warm)):
            if group:
                times = sorted(t["seconds"] for t in group)
                lines.append(
                    f"- {name}: {len(group)} runs, median {times[len(times) // 2]:.0f}s, "
                    f"range {times[0]:.0f}-{times[-1]:.0f}s"
                )
        lines.append("")
    for label, status, note in results:
        mark = "ok" if (args.out / f"{label}.png").exists() else "FAILED"
        lines.append(f"- **{label}** — {mark}{f' — {note}' if note else ''}")
        if mark == "FAILED":
            lines.append(f"  - {status}")
    index.write_text("\n".join(lines) + "\n")
    print(
        f"\n{sum((args.out / f'{r[0]}.png').exists() for r in results)}/{len(results)} rendered -> {index}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
