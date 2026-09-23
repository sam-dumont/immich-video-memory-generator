"""The block a run prints about itself when it finishes.

Two things a reader has to be able to tell apart, so the block labels them:

*Measured this run* -- the CLI wraps `run_editorial_source`, so its
wall-clock is honest local arithmetic. It is not in the run database:
`RunTracker` records only clip_extraction, assembly and music, all inside
`generate_memory`. Claiming a phase breakdown the database does not hold is
how a partial picture starts reading as the whole one.

*The model's bill* -- run-level totals, which `runs show` renders from the
database for the same run. Both surfaces agree because both count the same
thing; neither pretends to phases that do not exist.
"""

from __future__ import annotations

from immich_memories.analysis.llm_metrics import LLMCounters
from immich_memories.operations.storyboard import Storyboard, storyboard_lines

__all__ = ["render_llm_totals", "render_run_summary"]

_NO_SPEND = LLMCounters()


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _thousands(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _calls_part(counters: LLMCounters) -> str:
    """Say how the calls were carried only when more than one wire carried them."""
    if not counters.batch_calls:
        return f"{counters.calls} calls"
    realtime = counters.calls - counters.batch_calls
    return f"{counters.calls} calls ({realtime} realtime, {counters.batch_calls} batch)"


def _llm_lines(counters: LLMCounters) -> list[str]:
    """What the model cost, or nothing at all when it was never asked.

    A run with no LLM configured -- the recommended NAS setup -- should read
    as a normal run, not as one missing a section.
    """
    if not counters.calls and not counters.cache_hits:
        return []

    parts = [_calls_part(counters)]
    if counters.cache_hits:
        parts.append(f"{counters.cache_hits} answered from the judgment cache")
    if counters.prompt_tokens or counters.completion_tokens:
        parts.append(
            f"{_thousands(counters.prompt_tokens)} prompt / "
            f"{_thousands(counters.completion_tokens)} completion"
        )
    if counters.wall_seconds:
        parts.append(f"{_clock(counters.wall_seconds)} summed request time")

    lines = ["", "  LLM   " + " · ".join(parts)]
    if counters.unmetered_calls:
        noun = "call" if counters.unmetered_calls == 1 else "calls"
        lines.append(
            f"        Token usage missing for {counters.unmetered_calls} {noun}; totals are incomplete"
        )
    if counters.preparation_calls:
        lines.append(
            f"        Includes {counters.preparation_calls} preparation calls; reader pricing does not cover them"
        )
    if counters.reasoning_tokens:
        lines.append(
            f"        {_thousands(counters.reasoning_tokens)} of the completion tokens were reasoning"
        )
    if counters.batch_calls:
        lines.append(
            f"        {_thousands(counters.batch_prompt_tokens)} prompt / "
            f"{_thousands(counters.batch_completion_tokens)} completion of that came "
            "back from the provider's batch route"
        )
    if counters.truncated:
        # The sentence #600 needed on the night it started, instead of two
        # months later in a server log nobody was reading.
        call = "call" if counters.truncated == 1 else "calls"
        lines.append(
            f"        {counters.truncated} thinking {call} truncated at the "
            "token budget and retried without it"
        )
    return lines


_TIER_LINES = {
    "no_captions": "no_captions — every producer but the caption; reasons are facts, not sentences",
    "metadata_only": "metadata_only — no ONNX, no captions; every picture held to family viewing",
}


def _tier_lines(tier: str) -> list[str]:
    """What a reduced preparation tier changed about this cut, or nothing on the full one.

    The default tier prints no line for the same reason a run with no model prints
    no model line: a normal run should read as normal, not as one missing a section.
    """
    described = _TIER_LINES.get(tier)
    return ["", "  TIER  " + described] if described else []


def _review_lines(pictures: int, run_id: str | None) -> list[str]:
    """How many shots sit in the exposure head's grey zone, and where to read them.

    Zero is worth saying: it means the run looked, not that nothing looked. Nothing in
    the cut changed either way -- this is the owner's list, not another gate.
    """
    handle = run_id or "<run id>"
    shot = "picture" if pictures == 1 else "pictures"
    return [
        "",
        f"  CHECK {pictures} {shot} to check before sharing"
        + (f" · immich-memories runs why <asset id> --run {handle}" if pictures else ""),
    ]


def render_run_summary(
    *,
    total_seconds: float,
    analysis_seconds: float,
    generation_seconds: float,
    eligible: int,
    planned: int,
    counters: LLMCounters | None,
    preparation_tier: str = "full",
    storyboard: Storyboard | None = None,
    run_id: str | None = None,
    review_before_sharing: int = 0,
) -> str:
    """The end-of-run block, as printable text.

    `counters` may be None when nothing was collecting; handled here rather
    than at the call site, which sits in a function with no complexity
    headroom -- a single `or` there is enough to fail the gate.
    """
    counters = counters if counters is not None else _NO_SPEND
    lines = [
        f"Memory generated in {_clock(total_seconds)}",
        "",
        "  measured this run",
        f"    selection              {_clock(analysis_seconds):>8}   "
        f"{planned} planned from {eligible} candidates",
        f"    generation             {_clock(generation_seconds):>8}",
    ]
    lines.extend(_tier_lines(preparation_tier))
    lines.extend(_review_lines(review_before_sharing, run_id))
    lines.extend(_llm_lines(counters))
    lines.extend(_storyboard_lines(storyboard, run_id))
    return "\n".join(lines)


_SUMMARY_SHOTS = 8


def _storyboard_lines(board: Storyboard | None, run_id: str | None) -> list[str]:
    """The first shots of the cut in the order they play, and where to read the rest."""
    if board is None or not board.shots:
        return []
    lines = ["", f"  the cut, in order ({board.summary_label})"]
    lines.extend(storyboard_lines(board, limit=_SUMMARY_SHOTS))
    if len(board.shots) > _SUMMARY_SHOTS:
        rest = len(board.shots) - _SUMMARY_SHOTS
        handle = run_id or "<run id>"
        lines.append(f"  ... {rest} more: immich-memories runs story {handle}")
    elif run_id:
        lines.append(
            f"  why any picture is in or out: immich-memories runs why <asset id> --run {run_id}"
        )
    return lines


def render_llm_totals(metrics: dict) -> str:
    """The model's bill from a stored run, phrased exactly as the run block did.

    `runs show` and `auto status` read this from the database; the end-of-run
    block computes it live. Both go through the same renderer so the same run
    cannot be described two different ways on two surfaces.
    """
    from immich_memories.analysis.llm_metrics import LLMCounters

    counters = LLMCounters(
        calls=int(metrics.get("llm_calls", 0)),
        unmetered_calls=int(metrics.get("llm_unmetered_calls", 0)),
        preparation_calls=int(metrics.get("llm_preparation_calls", 0)),
        cache_hits=int(metrics.get("llm_cache_hits", 0)),
        prompt_tokens=int(metrics.get("llm_prompt_tokens", 0)),
        completion_tokens=int(metrics.get("llm_completion_tokens", 0)),
        reasoning_tokens=int(metrics.get("llm_reasoning_tokens", 0)),
        truncated=int(metrics.get("llm_truncated", 0)),
        wall_seconds=float(metrics.get("llm_wall_seconds", 0.0)),
    )
    return "\n".join(line for line in _llm_lines(counters) if line).strip()
