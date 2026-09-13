"""Read what a cell actually did, out of the text and the files it left behind.

Every number in the published table comes through here. Nothing is estimated: a
field the run did not report is None, and the summary names it under `unmeasured`
rather than filling it in. The parsers are written against the exact format
strings in `cli/_run_summary.py` and `cli/_generate_display.py`, so a change to
either breaks a test here rather than silently producing a plausible wrong number.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# "Memory generated in 3m 07s" / "... in 42s" -- `_run_summary._clock`.
_TOTAL = re.compile(r"^Memory generated in (.+?)\s*$", re.MULTILINE)
_SELECTION = re.compile(
    r"^\s*selection\s+(\S+(?: \d\ds)?)\s+(\d+) planned from (\d+) candidates\s*$", re.MULTILINE
)
_GENERATION = re.compile(r"^\s*generation\s+(\d+m \d\ds|\d+s)\s*$", re.MULTILINE)
_TIER = re.compile(r"^\s*TIER\s+(\S+)", re.MULTILINE)
_LLM = re.compile(r"^\s*LLM\s+(.+?)\s*$", re.MULTILINE)
_CLOCK = re.compile(r"^(?:(\d+)m )?(\d+)s$")
_TOKENS = re.compile(r"([\d.]+k|\d+) prompt / ([\d.]+k|\d+) completion")
_CALLS = re.compile(r"^(\d+) calls")
_CACHE_HITS = re.compile(r"(\d+) answered from the judgment cache")
# `saved_path_line` puts the path on the same line, or indented underneath when
# the line would pass 80 columns. The label is not anchored to the start of a
# line because the matrix runs `generate --quiet`, where `print_success` goes
# through logging and the formatter stamps the first line with a timestamp.
_SAVED = re.compile(r"Video saved to:(?:[ \t]+(\S.*?))?[ \t]*$", re.MULTILINE)

# `prepare` prints a rate table whose `total` row ends in `human_duration`:
# "total  133  0.4812  100%  64 s" / "... 12 min" / "... 1 h 4 min".
_ELAPSED = r"(?:(\d+) h )?(?:([\d.]+) min|([\d.]+) s)"
_PREPARE_TOTAL = re.compile(rf"^total\s+[\d,]+\s+[\d.]+\s+100%\s+{_ELAPSED}\s*$", re.MULTILINE)
# One producer's row of the same table. `share` is an em dash when the pass cost
# no measurable time at all, and `total` is excluded because it is not a producer.
_PREPARE_ROW = re.compile(
    rf"^(?!total\b)([a-z][\w-]*)\s+([\d,]+)\s+([\d.]+)\s+(?:([\d.]+)%|\S)\s+{_ELAPSED}\s*$",
    re.MULTILINE,
)
# Not anchored: `print_success` prints a tick in front of this line, and under
# --quiet a log timestamp instead.
_PREPARE_PICTURES = re.compile(r"([\d,]+) pictures prepared at ([\d.]+) s/picture\.")


@dataclass
class HostedUsage:
    """What a hosted reader reported about itself. Missing stays missing."""

    calls: int | None = None
    cache_hits: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    counted_exactly: bool | None = None
    wall_seconds: float | None = None
    # No provider in this tree returns a price with its completion, so this is
    # None everywhere and the summary says so out loud.
    est_cost_eur: float | None = None

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "counted_exactly": self.counted_exactly,
            "wall_seconds": self.wall_seconds,
            "est_cost_eur": self.est_cost_eur,
        }


@dataclass
class RunSummary:
    """The end-of-run block, parsed."""

    total_s: float | None = None
    selection_s: float | None = None
    render_s: float | None = None
    planned: int | None = None
    eligible: int | None = None
    tier: str | None = None
    usage: HostedUsage = field(default_factory=HostedUsage)
    video_path: str | None = None


def clock_seconds(text: str) -> float | None:
    """`3m 07s` or `42s` back to seconds, or None when it is neither."""
    match = _CLOCK.match(text.strip())
    if not match:
        return None
    minutes, seconds = match.groups()
    return float(int(minutes or 0) * 60 + int(seconds))


def _tokens(text: str) -> tuple[int | None, int | None, bool | None]:
    match = _TOKENS.search(text)
    if not match:
        return None, None, None
    values = []
    exact = True
    for raw in match.groups():
        if raw.endswith("k"):
            values.append(round(float(raw[:-1]) * 1000))
            exact = False
        else:
            values.append(int(raw))
    return values[0], values[1], exact


def parse_llm_line(text: str) -> HostedUsage:
    """The `LLM` line of a run summary. Absent means the reader never called out."""
    match = _LLM.search(text)
    if not match:
        return HostedUsage()
    body = match.group(1)
    calls = _CALLS.search(body)
    hits = _CACHE_HITS.search(body)
    tokens_in, tokens_out, exact = _tokens(body)
    tail = body.rsplit("·", 1)[-1].strip()
    return HostedUsage(
        calls=int(calls.group(1)) if calls else None,
        cache_hits=int(hits.group(1)) if hits else 0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        counted_exactly=exact,
        wall_seconds=clock_seconds(tail),
    )


def parse_saved_path(text: str) -> str | None:
    """The one place a finished run names its file. `generate` has no --json."""
    match = _SAVED.search(text)
    if not match:
        return None
    if match.group(1):
        return _expand(match.group(1))
    lines = text[match.end() :].lstrip("\n").splitlines()
    return _expand(lines[0].strip()) if lines else None


def _expand(shown: str) -> str:
    """`display_path` writes `~` for the home directory; put it back."""
    cleaned = shown.replace("\\[", "[").strip()
    return str(Path(cleaned).expanduser()) if cleaned.startswith("~") else cleaned


def parse_run_summary(text: str) -> RunSummary:
    """Every number the end-of-run block prints, and nothing it does not."""
    summary = RunSummary()
    if total := _TOTAL.search(text):
        summary.total_s = clock_seconds(total.group(1))
    if selection := _SELECTION.search(text):
        summary.selection_s = clock_seconds(selection.group(1))
        summary.planned = int(selection.group(2))
        summary.eligible = int(selection.group(3))
    if generation := _GENERATION.search(text):
        summary.render_s = clock_seconds(generation.group(1))
    if tier := _TIER.search(text):
        summary.tier = tier.group(1)
    summary.usage = parse_llm_line(text)
    summary.video_path = parse_saved_path(text)
    return summary


def _elapsed_seconds(hours: str | None, minutes: str | None, seconds: str | None) -> float:
    total = float(hours or 0) * 3600
    total += float(minutes) * 60 if minutes else float(seconds or 0)
    return round(total, 2)


def parse_prepare_seconds(text: str) -> float | None:
    """Elapsed preparation from the `total` row of the rate table `prepare` prints.

    `human_duration` rounds to whole seconds under 90 s and whole minutes above,
    so this is the number the product itself reports, at the product's precision.
    A finer one would need a flag `prepare` does not have.
    """
    match = _PREPARE_TOTAL.search(text)
    return _elapsed_seconds(*match.groups()) if match else None


def parse_models_fetch_seconds(text: str) -> float | None:
    """The container's own stopwatch over `models fetch`, one whole-second count.

    Written by the container rather than timed from here because the ssh round
    trip and the pod's scheduling are not part of what a download cost. A file
    with anything else in it means the fetch died before the echo and the phase
    stays unmeasured, which is the truth.
    """
    stripped = text.strip()
    return float(stripped) if stripped.isdigit() else None


def parse_prepared_producers(text: str) -> list[dict]:
    """Each producer's row of the rate table, in the order preparation ran them.

    The per-picture total hides which stage was expensive, and that is the whole
    question on a low-power box: captions took every measurable second of the
    first Mac cell, and the table is where a row can say so.
    """
    return [
        {
            "producer": producer,
            "pending": int(pending.replace(",", "")),
            "seconds_per_picture": float(rate),
            "share_pct": float(share) if share else None,
            "seconds": _elapsed_seconds(hours, minutes, seconds),
        }
        for producer, pending, rate, share, hours, minutes, seconds in _PREPARE_ROW.findall(text)
    ]


def parse_prepared_pictures(text: str) -> tuple[int | None, float | None]:
    """How many pictures a `prepare` banked, and what each one cost it."""
    match = _PREPARE_PICTURES.search(text)
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), float(match.group(2))


# `/usr/bin/time` writes its report to stderr after the command has exited.
# BSD (`-l`, macOS) counts bytes, GNU (`-v`) counts kilobytes.
_BSD_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size", re.MULTILINE)
_GNU_RSS = re.compile(r"^\s*Maximum resident set size \(kbytes\):\s*(\d+)", re.MULTILINE)


def parse_time_peak_rss_mb(text: str) -> float | None:
    """One step's peak resident memory, from whichever `/usr/bin/time` the host has."""
    if match := _BSD_RSS.search(text):
        return round(int(match.group(1)) / 1_048_576, 1)
    if match := _GNU_RSS.search(text):
        return round(int(match.group(1)) / 1024, 1)
    return None


def parse_cgroup_peak_rss_mb(text: str) -> float | None:
    """The peak the kernel counted, from `memory.peak` (v2) or `max_usage_in_bytes` (v1)."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return round(int(first) / 1_048_576, 1) if first.isdigit() else None


def parse_cgroup_cpu_seconds(text: str) -> float | None:
    """CPU seconds, from `cpu.stat` (microseconds, v2) or `cpuacct.usage` (nanoseconds, v1)."""
    if match := re.search(r"^usage_usec (\d+)$", text, re.MULTILINE):
        return round(int(match.group(1)) / 1_000_000, 2)
    stripped = text.strip()
    return round(int(stripped) / 1_000_000_000, 2) if stripped.isdigit() else None


def probe_video(path: Path) -> dict | None:
    """Duration, resolution and size, straight from ffprobe. None when it cannot read it."""
    if not path.is_file():
        return None
    proc = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=width,height,codec_name,codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout or "{}")
    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    fmt = data.get("format", {})
    return {
        "duration_s": round(float(fmt.get("duration", 0.0)), 2) or None,
        "size_bytes": int(fmt.get("size", 0)) or None,
        "width": video.get("width"),
        "height": video.get("height"),
        "codec": video.get("codec_name"),
    }


def read_cut(attempt_dir: Path) -> dict:
    """The cut as the run recorded it: ids in play order, the editor's reason for each.

    Reuses the storyboard reader the terminal and the web UI already share, so the
    matrix cannot disagree with `runs story` about what shipped.
    """
    from immich_memories.operations.storyboard import read_storyboard

    board = read_storyboard(attempt_dir)
    if board is None:
        return {"selected": [], "thesis": None}
    return {
        "thesis": board.thesis or None,
        "selected": [
            {
                "asset_id": shot.asset_id,
                "taken": shot.taken,
                "seconds": shot.seconds,
                "motion": shot.motion,
                "story": shot.story_title,
                "reason": shot.reason,
            }
            for shot in board.shots
        ],
    }


def read_losses(attempt_dir: Path) -> dict:
    """What the reader said it let go, and the warnings it wanted read first."""
    from immich_memories.cli._runs_reading import read_trace

    trace = read_trace(attempt_dir)
    if trace is None:
        return {"warnings": [], "dropped": [], "lost_favourites": None}
    dropped = []
    for asset_id in sorted(trace.clips):
        story = trace.story_of(asset_id)
        if story.shipped or not story.dropped_at:
            continue
        dropped.append(
            {"asset_id": asset_id, "pass": story.dropped_at, "reason": story.reason or ""}
        )
    return {
        "warnings": list(trace.warnings),
        "dropped": dropped,
        "lost_favourites": None if trace.lost_favourites is None else len(trace.lost_favourites),
    }


def latest_attempt(cache_dir: Path, memory_key: str) -> Path | None:
    """The newest attempt for a cell, or the one `latest-attempt.private.json` names."""
    root = Path(cache_dir) / "editorial-runs" / memory_key
    pointer = root / "latest-attempt.private.json"
    if pointer.is_file():
        try:
            named = Path(json.loads(pointer.read_text())["directory"])
        except (KeyError, ValueError, TypeError):
            named = root
        if named.is_dir():
            return named
    attempts = sorted((root / "attempts").glob("*")) if (root / "attempts").is_dir() else []
    return attempts[-1] if attempts else None


_ANONYMOUS = "asset"


def anonymize(record: dict) -> dict:
    """Strip anything that names a picture, a person or a place, keeping the shape.

    Asset ids become stable positional handles so overlap between two cells is
    still computable after the strip; free text the reader wrote is dropped
    entirely, because the gate cannot see what a scene description contains.
    """
    handles: dict[str, str] = {}

    def handle(asset_id: str) -> str:
        return handles.setdefault(asset_id, f"{_ANONYMOUS}-{len(handles) + 1:03d}")

    return _walk(record, handle)


def _walk(value: object, handle) -> object:  # noqa: ANN001
    if isinstance(value, dict):
        return {key: _anonymize_field(key, item, handle) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, handle) for item in value]
    return value


_DROPPED_FIELDS = frozenset({"reason", "thesis", "story", "taken", "warnings", "filename", "path"})


def _anonymize_field(key: str, value: object, handle) -> object:  # noqa: ANN001
    if key == "asset_id" and isinstance(value, str):
        return handle(value)
    if key in _DROPPED_FIELDS:
        return None if not isinstance(value, list) else []
    if key == "selected_asset_ids" and isinstance(value, list):
        return [handle(str(item)) for item in value]
    return _walk(value, handle)
