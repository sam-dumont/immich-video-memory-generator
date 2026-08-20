"""Render one short video per capability, so a release can be watched.

Unit tests say a filter string contains `drawtext`; only a rendered file says
the caption is legible, in the right corner, and not underneath the platform's
own UI. This walks `capability_matrix.yaml`, runs each row through the real CLI
against the real library, and writes an index next to the results.

Local only. Outputs land under `output/` which is gitignored, because they
contain real footage.

    make capability-matrix ALBUM="Some Album"
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from immich_memories.config_loader import _TIER2_SECTIONS, Config
from immich_memories.config_presets import PRESETS

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).with_suffix(".yaml")


@dataclass
class Result:
    name: str
    why: str
    command: str
    seconds: float
    output: Path | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


def _pinned_config(source: Path | None, dest: Path, pins: dict) -> Path:
    """A copy of the config with the settings the matrix varies pinned to known values.

    WHY at all: inheriting the developer's config means the rows test that
    config, not the code. The first sweep ran every row at 4K because one local
    line said so -- never touching 1080p, which is the shipped default and what
    a NAS actually runs -- and two rows asserted flags their config already set,
    so they re-rendered the baseline under a different name and reported "ok".

    WHY the section walk: a tier-2 section may sit at top level (legacy) or
    under `advanced:`, and the loader resolves the clash with `if key not in
    data` -- top level wins. Writing the modern spelling into a config using the
    old one leaves the edit inert.
    """
    source = source or Config.get_default_path()
    if not source.exists():
        raise SystemExit(
            f"{source} does not exist, and the sweep needs a real config to copy "
            "for its Immich credentials. Pass --config explicitly."
        )
    data = yaml.safe_load(source.read_text()) or {}

    # WHY: `apply_preset` fills only fields the user has NOT set, so any key the
    # local config happens to carry silently outranks the preset -- and the row
    # renders a partial profile while claiming to show the whole one. Clearing
    # the keys the preset owns hands them back to it. Explicit pins are applied
    # after, so a row can still override one.
    if "preset" in pins:
        for section_name, values in PRESETS[pins["preset"]].items():
            section = data.get(section_name)
            if isinstance(section, dict):
                for field in values:
                    section.pop(field, None)

    for dotted, value in pins.items():
        section, _, leaf = dotted.partition(".")
        if not leaf:
            data[section] = value
            continue
        if section in data:
            target = data[section]
        elif section in data.get("advanced", {}):
            target = data["advanced"][section]
        elif section in _TIER2_SECTIONS:
            target = data.setdefault("advanced", {}).setdefault(section, {})
        else:
            target = data.setdefault(section, {})
        target[leaf] = value

    dest.write_text(yaml.safe_dump(data, sort_keys=False))
    return dest


def _low_power_prefix() -> list[str]:
    """Run a row on the efficiency cores, to stand in for a NAS.

    macOS has no taskset and thread affinity is a hint the scheduler may ignore,
    so a core count cannot be pinned. Background QoS can be, and it is honoured:
    the same 1080p encode takes 1.36s unrestricted and 5.78s under it, because
    the process tree is kept off the performance cores. Children inherit it, so
    ffmpeg is covered without touching its -threads flags.

    Absent elsewhere; the row then renders at full speed rather than not at all.
    """
    return ["taskpolicy", "-b"] if shutil.which("taskpolicy") else []


def _first_real_error(proc: subprocess.CompletedProcess[str]) -> str:
    """The line that explains the failure, not whatever printed last.

    The tail of a failed run is usually teardown noise -- the first smoke test
    reported `ggml_metal_free: deallocating` for what was actually a 404 from
    Immich.
    """
    lines = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()
    for marker in ("[ERROR]", "Traceback", "Error:", "error:"):
        hits = [ln.strip() for ln in lines if marker in ln]
        if hits:
            return hits[-1][:300]
    return lines[-1][:300] if lines else ""


def _frame_signature(path: Path) -> str:
    """A hash of one decoded frame, for telling two renders apart.

    Container timestamps differ between runs, so hashing the file says nothing;
    two rows that rendered identical video had different SHAs and byte-identical
    sizes. Decoding a frame compares what actually reaches the viewer.
    """
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "5", "-i", str(path), "-frames:v", "1", "-f", "md5", "-"],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() or f"unreadable:{path.name}"


def _duplicate_groups(paths: list[Path]) -> list[list[str]]:
    """Names of rows whose renders are pixel-identical, grouped.

    A duplicate means a row asserted something the baseline already sets, so it
    silently re-rendered the baseline and reported "ok".
    """
    by_signature: dict[str, list[str]] = {}
    for path in paths:
        by_signature.setdefault(_frame_signature(path), []).append(path.name)
    return [names for names in by_signature.values() if len(names) > 1]


def _rendered_file(out_dir: Path, name: str, since: float) -> Path | None:
    """Where the render actually landed.

    `--output` names a file, but the CLI writes into a run directory beside it
    named after the recipe (`<name>_<hash>_<timestamp>_<id>/`) so a rerun of the
    same recipe replaces itself. Find the newest video carrying this row's name.
    """
    videos = [p for suffix in ("*.mp4", "*.mov", "*.mkv") for p in out_dir.rglob(suffix)]
    matches = [p for p in videos if p.name.startswith(name)]
    if not matches:
        # WHY: `--from-album` names the file after the album, not after `--output`,
        # so the first sweep reported "wrote no file" for a render sitting on disk
        # as album_<name>_<hash>.mp4. Anything written since this row started is it.
        matches = [p for p in videos if p.stat().st_mtime >= since]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _run(
    row: dict,
    common: list[str],
    out_dir: Path,
    album: str,
    config: Path | None,
    baseline: dict,
) -> Result:
    name = row["name"]
    target = out_dir / f"{name}.mp4"
    args = [str(a).replace("{album}", album) for a in row["args"]]
    pins = {**baseline, **row.get("config", {})}
    root = ["--config", str(_pinned_config(config, out_dir / f"{name}.yaml", pins))]

    cmd = [
        *(_low_power_prefix() if row.get("low_power") else []),
        "uv",
        "run",
        "immich-memories",
        *root,
        "generate",
        *([] if row.get("skip_common") else common),
        *args,
        "--output",
        str(target),
        "--quiet",
    ]
    started = time.monotonic()
    wall = time.time()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    elapsed = time.monotonic() - started

    error = None
    produced = None
    if proc.returncode != 0:
        error = _first_real_error(proc) or f"exit {proc.returncode}"
    else:
        produced = _rendered_file(out_dir, name, wall)
        if produced is None:
            error = "command succeeded but wrote no file"

    printable = " ".join(cmd[cmd.index("generate") :])
    return Result(name, row["why"], printable, elapsed, produced, error)


def _write_index(results: list[Result], out_dir: Path) -> Path:
    lines = [
        f"# Capability matrix — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{sum(r.ok for r in results)}/{len(results)} rendered."
        f" Total {sum(r.seconds for r in results) / 60:.1f} min.",
        "",
        "| # | Capability | Why it is here | Time | Result |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        outcome = f"[{r.output.name}]({r.output.name})" if r.ok else f"**failed** — {r.error}"
        lines.append(f"| {i} | `{r.name}` | {r.why} | {r.seconds:.0f}s | {outcome} |")
    duplicates = _duplicate_groups([r.output for r in results if r.ok and r.output])
    if duplicates:
        lines += ["", "## Identical renders", ""]
        lines += [
            "These rows produced the same video, so whatever they were meant to vary "
            "is already the baseline:",
            "",
        ]
        lines += [f"- {' = '.join(names)}" for names in duplicates]

    lines += ["", "## Commands", ""]
    lines += [f"- `{r.name}`: `immich-memories {r.command}`" for r in results]
    index = out_dir / "index.md"
    index.write_text("\n".join(lines) + "\n")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--album", default="", help="album name for the --from-album row")
    parser.add_argument("--config", type=Path, default=None, help="config file to generate with")
    parser.add_argument("--only", default="", help="substring filter over row names")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    opts = parser.parse_args()

    manifest = yaml.safe_load(MANIFEST.read_text())
    rows = [r for r in manifest["rows"] if opts.only in r["name"]]
    if not rows:
        print(f"no rows match {opts.only!r}", file=sys.stderr)
        return 2

    skipped = [r["name"] for r in rows if "{album}" in str(r["args"]) and not opts.album]
    rows = [r for r in rows if r["name"] not in skipped]
    for name in skipped:
        print(f"skipping {name}: needs --album", file=sys.stderr)

    out_dir = (
        opts.out or REPO_ROOT / "output" / "capability-matrix" / f"{datetime.now():%Y%m%d-%H%M}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"writing to {out_dir}")

    results = []
    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row['name']} ...", flush=True)
        result = _run(
            row,
            manifest["common"],
            out_dir,
            opts.album,
            opts.config,
            manifest.get("baseline_config", {}),
        )
        results.append(result)
        print(
            f"    {'ok' if result.ok else 'FAILED: ' + (result.error or '')} ({result.seconds:.0f}s)"
        )

    index = _write_index(results, out_dir)
    print(f"\n{sum(r.ok for r in results)}/{len(results)} rendered — {index}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
