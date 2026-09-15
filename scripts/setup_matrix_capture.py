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
from datetime import datetime
from pathlib import Path

from setup_matrix_plan import COLD, PRIMED

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
# ...unless something else did the work, in which case `elapsed` is not the last
# column. `preparation_report.rate_report` adds `service s/pic` to the right of
# it as soon as any producer reports what another machine charged itself: a rate
# for the rows that ran there, an em dash for the rows that ran here. A pattern
# anchored to the end of `elapsed` stops matching the day that column appears,
# and `k8s-gpu-t1000` published a null `prepare_cold_s` and no producers at all
# over a table that was sitting in its log, complete, the whole time.
_SERVICE_RATE = r"(?:\s+(?:[\d.]+|—))?"
_PREPARE_TOTAL = re.compile(
    rf"^total\s+[\d,]+\s+[\d.]+\s+100%\s+{_ELAPSED}{_SERVICE_RATE}\s*$", re.MULTILINE
)
# One producer's row of the same table. `share` is an em dash when the pass cost
# no measurable time at all, and `total` is excluded because it is not a producer.
_PREPARE_ROW = re.compile(
    rf"^(?!total\b)([a-z][\w-]*)\s+([\d,]+)\s+([\d.]+)\s+(?:([\d.]+)%|\S)\s+"
    rf"{_ELAPSED}{_SERVICE_RATE}\s*$",
    re.MULTILINE,
)
# Not anchored: `print_success` prints a tick in front of this line, and under
# --quiet a log timestamp instead.
_PREPARE_PICTURES = re.compile(r"([\d,]+) pictures prepared at ([\d.]+) s/picture\.")
# The same line, as the end of one `prepare` invocation rather than as numbers.
_PREPARE_END = re.compile(r"[\d,]+ pictures prepared at [\d.]+ s/picture\.")

# What captioned this cell's pictures. A matrix row compares readers on the
# assumption that both were handed the same facts, and a bank filled by two
# captioners breaks that assumption silently — so the count travels with the
# producers rather than staying in the log nobody diffs.
_CAPTION_ORIGINS = re.compile(
    r"caption origins: (\d+) distinct over (\d+) captions( MIXED)? \[(.*)\]"
)

# What drew the title screens. `titles/kernels.init_kernels` prints one of these
# two lines once per process, and the titles are the phase a GPU helps most.
_TITLE_BACKEND = re.compile(r"Title kernels: \S+ \S+ on the (\S+) backend")
_PIL_TITLES = "title screens use the PIL renderer"
PIL_RENDERER = "PIL"
# What encoded the film. `assembly_engine` names the encoder on its way in, and
# it is the only line that says what actually ran: the first cluster Jobs picked
# the CUDA title backend and still encoded in software, because the NVIDIA
# runtime exposed `compute,utility` and every NVENC probe died on
# "Terminating thread with return code -22 (Invalid argument)".
_ASSEMBLY_ENCODER = re.compile(r"Streaming (?:\S+ )?(?:SDR|HDR) assembly with (\S+)")
# The detection line, which is all a run that never reached the assembly leaves.
# It names a backend and not an encoder, and is recorded as the backend word.
_HWACCEL = re.compile(r"Detected \S+ hardware acceleration: (\w+):")
_NO_HWACCEL = "No hardware acceleration detected, using software encoding"
SOFTWARE_ENCODE = "software"


@dataclass
class HostedUsage:
    """What a hosted reader reported about itself. Missing stays missing."""

    calls: int | None = None
    cache_hits: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    counted_exactly: bool | None = None
    wall_seconds: float | None = None
    # A subset of tokens_out that a reasoning model added on its own account. One
    # measured reply put 13,469 of them against a 5,400-token prompt, billed at
    # the completion rate, so a cost line that does not name them explains nothing.
    reasoning_tokens: int | None = None
    # Prompt tokens nobody counted, worked out from the size of the request the
    # run kept. Never folded into `tokens_in`: a reconstructed row has a measured
    # half and a guessed half, and the report has to be able to tell them apart.
    estimated_prompt_tokens: int | None = None
    # Where these numbers came from: "record" is the run's own llm-usage.json,
    # "reconstructed" is the planner block plus the pre-planner outcomes added up
    # after the fact, and "log" is the rounded end-of-run line. None means the
    # cell never asked a model.
    usage_source: str | None = None
    # Tiles the run actually sent a model. Not on the LLM line: the end-of-run
    # block counts calls and tokens, and this is read off the attempt's own plan.
    images_sent: int | None = None
    # No provider in this tree returns a price with its completion, so the capture
    # leaves this empty and the summary fills it from the manifest's price list,
    # in whatever currency that shop publishes in.
    est_cost: float | None = None
    cost_currency: str | None = None

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "counted_exactly": self.counted_exactly,
            "wall_seconds": self.wall_seconds,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "usage_source": self.usage_source,
            "images_sent": self.images_sent,
            "est_cost": self.est_cost,
            "cost_currency": self.cost_currency,
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


def parse_title_backend(text: str) -> str | None:
    """Which backend drew the title screens, or None when the run never said.

    The cluster Jobs render titles on CUDA today with no GPU request at all,
    because the device plugin hands out a shared card and the kernel library
    takes what it finds. That is half the render, and the table had no column
    for it.
    """
    if match := _TITLE_BACKEND.search(text):
        return match.group(1)
    return PIL_RENDERER if _PIL_TITLES in text else None


def parse_encoder(text: str) -> str | None:
    """Which encoder the film was written with, or None when the run never said.

    The assembly names it, and that is the answer. Failing that, the detection
    line names a backend rather than an encoder, so the backend word is what gets
    recorded: turning `nvidia` into `h264_nvenc` here would be this file guessing,
    and the whole point of it is that it does not.
    """
    if match := _ASSEMBLY_ENCODER.search(text):
        return match.group(1)
    if _NO_HWACCEL in text:
        return SOFTWARE_ENCODE
    match = _HWACCEL.search(text)
    return match.group(1) if match else None


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
        usage_source="log",
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


def parse_caption_origins(text: str) -> dict | None:
    """How many distinct captioners stand behind this cell's captions, and which.

    `prepare` prints one line per run, so a cell that ran it twice reports the
    last word on the bank rather than the cold pass's view of it.
    """
    matches = _CAPTION_ORIGINS.findall(text)
    if not matches:
        return None
    distinct, captions, mixed, labels = matches[-1]
    return {
        "distinct": int(distinct),
        "captions": int(captions),
        "mixed": bool(mixed),
        "labels": [label.strip() for label in labels.split("; ") if label.strip()],
    }


def parse_prepared_pictures(text: str) -> tuple[int | None, float | None]:
    """How many pictures a `prepare` banked, and what each one cost it."""
    match = _PREPARE_PICTURES.search(text)
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), float(match.group(2))


def prepare_phases(text: str) -> list[str]:
    """One slice per `prepare` invocation in a stream that carries more than one.

    A remote cell tees each phase into its own file AND prints both into one
    stdout, and that stdout is all there is when the copy-out loses the files.
    The rate table has the same shape in both, so a `findall` over the pair hands
    back one cell's producers twice: cold and warm are separated first, on the
    line each invocation ends with.
    """
    phases, start = [], 0
    for match in _PREPARE_END.finditer(text):
        phases.append(text[start : match.end()])
        start = match.end()
    return phases


def parse_cache_primed(text: str) -> bool | None:
    """Whether a cell's cache already held a run, out of the word the lane wrote.

    Both remote lanes answer in one word, from different places: the cluster's
    container reads a marker of its own, and the NAS looks at the cache directory
    in the step that runs before it would have created it. Anything else (an ssh
    error, a file the copy-out truncated) leaves the field unmeasured.
    """
    for line in text.splitlines():
        if (word := line.strip()) in {PRIMED, COLD}:
            return word == PRIMED
    return None


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


# The reading contracts leave two kinds of trace. A repair is a second question
# with the rejection spelled out, and the judge writes one request transcript per
# question, so the `-repair` stages ARE the repairs. An answer nothing recovered
# leaves a failure transcript instead. The episode reader has neither: it warns,
# once per distinct reason (#913), and only into the run log.
CALLS = "calls"
_REPAIR_ASKED = "*-repair*.request.private.txt"
_ANSWER_REFUSED = "*.failure.private.json"
_ANSWER_UNREADABLE = "*json-failure*.private.json"
_EPISODE_REFUSED = re.compile(r"(text episode provider failed \([^)]*\): .+?)\s*$", re.MULTILINE)


PLAN_FILE = "plan.private.json"


def read_images_sent(attempt_dir: Path) -> int | None:
    """Tiles this run sent a model, summed over the metrics blocks the plan keeps.

    The story-first reader is mostly text and mostly is not never: the structure
    pass demands 800 px tiles for the pictures it cannot settle on paper, a few
    dozen in a month, and the pair confirmer and the story-motion check ask for
    more. Those are image tokens somebody is billed for, so the cost line says how
    many there were rather than implying the bill was all prose.
    """
    plan = attempt_dir / PLAN_FILE
    if not plan.is_file():
        return None
    try:
        record = json.loads(plan.read_text())
    except ValueError:
        return None
    counted = [
        block["images_sent"]
        for block in record.values()
        if isinstance(block, dict) and isinstance(block.get("images_sent"), int)
    ]
    return sum(counted) if counted else None


# `analysis/llm_usage_record.USAGE_FILE`. Named here rather than imported so the
# capture keeps running with nothing but the standard library on a remote host.
LLM_USAGE_FILE = "llm-usage.json"

# What the record calls a number, and what this table calls it.
_FROM_RECORD = {
    "calls": "calls",
    "cache_hits": "cache_hits",
    "prompt_tokens": "tokens_in",
    "completion_tokens": "tokens_out",
    "reasoning_tokens": "reasoning_tokens",
    "wall_seconds": "wall_seconds",
}


def apply_exact_usage(usage: dict, attempt_dir: Path) -> None:
    """Put the run's own counts over the rounded ones the end-of-run line printed.

    `_run_summary` renders `115.6k prompt` for a person at a terminal, and this
    table multiplies tokens by a price per million. Eight tokens of rounding is
    not much; a hundred cells of it is a number nobody can check.

    A cell whose run left no record -- an older run, or a remote one whose
    copy-out missed the file -- keeps what the line said and goes on reporting
    `usage_source: log`, which is the field to read before trusting a cost to
    four digits.
    """
    source = attempt_dir / LLM_USAGE_FILE
    if not source.is_file():
        _apply_reconstruction(usage, attempt_dir)
        return
    try:
        counted = json.loads(source.read_text())
    except ValueError:
        return
    if not isinstance(counted, dict):
        return
    for name, field_name in _FROM_RECORD.items():
        if isinstance(counted.get(name), int | float):
            usage[field_name] = counted[name]
    usage["counted_exactly"] = True
    usage["usage_source"] = "record"


def _apply_reconstruction(usage: dict, attempt_dir: Path) -> None:
    """Second choice, ahead of the log line: what the attempt's own artifacts add up to."""
    rebuilt = reconstruct_usage(attempt_dir)
    if rebuilt is None:
        return
    for name, value in rebuilt.as_dict().items():
        if value is not None:
            usage[name] = value


# Bytes of a kept request per prompt token. Measured, not assumed: across the
# five hosted cells that finished before the usage record existed, the planner's
# own request files divided by its own text prompt tokens gave 3.54, 3.55, 3.81,
# 3.91 and 4.01. One divisor for all of them is worth about +/-7%, which is why
# what it produces is kept out of the counted total and the row says it is not exact.
BYTES_PER_PROMPT_TOKEN = 3.8

_PRE_PLANNER_DIR = "pre-planner-calls"
_OUTCOME = "*.outcome.private.json"


def _reply_seconds(outcome: dict) -> float:
    """How long one pre-planner read took, or zero when it did not say."""
    try:
        started = datetime.fromisoformat(outcome["started_at"])
        finished = datetime.fromisoformat(outcome["finished_at"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(0.0, (finished - started).total_seconds())


@dataclass
class _PrePlannerSpend:
    """What the reads before the planner cost, as far as their outcomes recorded it."""

    calls: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    request_bytes: int = 0
    wall_seconds: float = 0.0


def _pre_planner_spend(attempt_dir: Path) -> _PrePlannerSpend:
    """Calls, completion, reasoning, request bytes and wall of the reads before the planner.

    Episode reads and the period account run before `plan_structure` opens the
    scope that wrote `llm_metrics`, so nothing else in the attempt has them. An
    outcome whose transport never came back carries no reply and is not counted:
    the deepseek cell raised on one episode read, and its log shows 105 replies
    against 9 outcome files.
    """
    spend = _PrePlannerSpend()
    for path in sorted((attempt_dir / _PRE_PLANNER_DIR).glob(_OUTCOME)):
        try:
            outcome = json.loads(path.read_text()) or {}
        except ValueError:
            continue
        reply = outcome.get("reply")
        if not isinstance(reply, dict) or reply.get("completion_tokens") is None:
            continue
        spend.calls += 1
        spend.completion_tokens += int(reply.get("completion_tokens") or 0)
        spend.reasoning_tokens += int(reply.get("reasoning_tokens") or 0)
        spend.wall_seconds += _reply_seconds(outcome)
        request = path.with_name(path.name.replace(".outcome.private.json", ".request.private.txt"))
        if request.is_file():
            spend.request_bytes += request.stat().st_size
    return spend


def reconstruct_usage(attempt_dir: Path) -> HostedUsage | None:
    """What a run cost, added up after the fact from what it left behind.

    For the cells that finished before a run wrote its own usage record and are
    too expensive to run again. `plan.private.json` holds the planner scope
    exactly, and the pre-planner outcomes hold the reads that happened before
    that scope opened. On the five hosted cells this was written against, the two
    together came to 93, 105, 111, 217 and 198 calls, matching every HTTP 200
    their logs recorded, to the call.

    `picture_facts_metrics` is deliberately not added. The facts are read inside
    the scope that wrote `llm_metrics`, so it is a view of part of that block
    rather than a second bill: adding it would charge the glm demo cell for 125
    calls against the 93 it made. The one number it contributes is `images_sent`,
    which `read_images_sent` already carries.

    None when the run wrote its own record -- that one is exact and wins -- or
    when there is no plan to read.
    """
    if (attempt_dir / LLM_USAGE_FILE).is_file():
        return None
    plan = attempt_dir / PLAN_FILE
    if not plan.is_file():
        return None
    try:
        planner = (json.loads(plan.read_text()) or {}).get("llm_metrics")
    except ValueError:
        return None
    if not isinstance(planner, dict):
        return None
    pre = _pre_planner_spend(attempt_dir)
    total_calls = int(planner.get("llm_calls") or 0) + pre.calls
    cache_hits = int(planner.get("llm_cache_hits") or 0)
    if not (total_calls or cache_hits):
        # `provider_metrics` persists measured zeroes, so a rules cell has a full
        # block of them. "Never asked a model" must not become a bill of nothing.
        return None
    return HostedUsage(
        calls=total_calls,
        cache_hits=cache_hits,
        tokens_in=int(planner.get("llm_prompt_tokens") or 0),
        tokens_out=int(planner.get("llm_completion_tokens") or 0) + pre.completion_tokens,
        # Only the pre-planner share: these runs never recorded a reasoning count
        # inside the planner scope, so this is a floor and `usage_source` says so.
        reasoning_tokens=pre.reasoning_tokens,
        estimated_prompt_tokens=round(pre.request_bytes / BYTES_PER_PROMPT_TOKEN),
        counted_exactly=False,
        wall_seconds=round(float(planner.get("llm_wall_seconds") or 0) + pre.wall_seconds, 3),
        usage_source="reconstructed",
    )


def read_contract_health(attempt_dir: Path | None, log_text: str) -> dict:
    """How often a reading contract refused this cell's reader, and how often it asked again.

    Overlap says which pictures a reader chose. This says what it took to get an
    answer out of it in the shape the contract asked for, which is the other half
    of whether a model is any good: a hosted qwen3-30b once answered a pick with
    the whole offered row, twice, and killed a run with nothing in the table to
    show for it.

    A cell that never called a model leaves no transcripts at all, and reports
    null rather than a zero that would read as a clean reader.
    """
    calls = attempt_dir / CALLS if attempt_dir else None
    if calls is None or not calls.is_dir():
        return {"rejections": None, "repairs": None}
    repairs = len(list(calls.glob(_REPAIR_ASKED)))
    unrecovered = len(list(calls.glob(_ANSWER_REFUSED))) + len(list(calls.glob(_ANSWER_UNREADABLE)))
    # Deduplicated on the reason, the way the reader itself reports it: a remote
    # lane carries the same stdout in more than one file and both are read.
    reasons = {match.group(1) for match in _EPISODE_REFUSED.finditer(log_text)}
    return {"rejections": repairs + unrecovered + len(reasons), "repairs": repairs}


# What a run fetched to cut with, and how many clips the film was made of. Both
# are read off the run's own log, and both are only ever asked for when the
# attempt directory did not come back: `k8s-gpu-t1000` published an empty cut
# beside a 54.5 s film because a truncated tar left the attempt on the volume.
#
# `asset_service` builds the original's URL, and a preview is `/thumbnail`, so
# `/original` is exactly the set of pictures the cut downloaded. The order is the
# order they were FETCHED, which is not the order they play: the videos go first
# and concurrently. Nothing here claims otherwise.
# What a record says when its cut came out of a log rather than out of the
# editor's own attempt. Read by the summary, which will not publish a running
# order for one: a fetch order is not a play order.
CUT_FROM_LOG = "generate log"
_DOWNLOADED = re.compile(r"/api/assets/([^/\s\"']+)/original")
_DOWNLOAD_START = "Downloading clips"
# `generate._log_pipeline_timing` counts the clips the film is made of, which is
# the cut without the title and ending screens the assembler adds.
_FILM_CLIPS = re.compile(r"ipeline timing \((\d+) clips,")


def downloaded_asset_ids(text: str) -> list[str]:
    """Every picture the run downloaded to cut with, first fetch first.

    Only the lines after the download phase begins, so the thumbnails preparation
    pulled for the whole month are not mistaken for the cut.
    """
    start = text.find(_DOWNLOAD_START)
    body = text[start:] if start >= 0 else text
    return list(dict.fromkeys(_DOWNLOADED.findall(body)))


def film_clip_count(text: str) -> int | None:
    """How many clips the film was assembled from, as the run counted them."""
    matches = _FILM_CLIPS.findall(text)
    return int(matches[-1]) if matches else None


def latest_attempt(runs_dir: Path, memory_key: str) -> Path | None:
    """The newest attempt for a cell, or the one `latest-attempt.private.json` names.

    `runs_dir` holds one directory per memory key. The mac lane points it at its
    cell's `editorial-runs`; a remote cell's container copies the same directory
    into its output volume, so the copy-out lands it under the cell's own
    `attempts/`. Same tree, same reader, different root. The pointer file names a
    path inside the container, so a remote read falls back to the newest on disk.
    """
    root = Path(runs_dir) / memory_key
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
