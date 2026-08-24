"""The block a run prints about itself when it finishes.

Two things a reader has to be able to tell apart, so the block labels them:

*Measured this run* -- the CLI wraps `run_analysis` and `run_selection`, so
their wall-clock is honest local arithmetic. It is not in the run database:
`RunTracker` records only clip_extraction, assembly and music, all inside
`generate_memory`. Claiming a phase breakdown the database does not hold is
how a partial picture starts reading as the whole one.

*The model's bill* -- run-level totals, which `runs show` renders from the
database for the same run. Both surfaces agree because both count the same
thing; neither pretends to phases that do not exist.
"""

from __future__ import annotations

from immich_memories.analysis.llm_metrics import LLMCounters

__all__ = ["render_llm_totals", "render_run_summary"]

_NO_SPEND = LLMCounters()


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _thousands(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _llm_lines(counters: LLMCounters) -> list[str]:
    """What the model cost, or nothing at all when it was never asked.

    A run with no LLM configured -- the recommended NAS setup -- should read
    as a normal run, not as one missing a section.
    """
    if not counters.calls and not counters.cache_hits:
        return []

    parts = [f"{counters.calls} calls"]
    if counters.cache_hits:
        parts.append(f"{counters.cache_hits} answered from the judgment cache")
    if counters.prompt_tokens or counters.completion_tokens:
        parts.append(
            f"{_thousands(counters.prompt_tokens)} prompt / "
            f"{_thousands(counters.completion_tokens)} completion"
        )
    if counters.wall_seconds:
        parts.append(_clock(counters.wall_seconds))

    lines = ["", "  LLM   " + " · ".join(parts)]
    if counters.truncated:
        # The sentence #600 needed on the night it started, instead of two
        # months later in a server log nobody was reading.
        call = "call" if counters.truncated == 1 else "calls"
        lines.append(
            f"        {counters.truncated} thinking {call} truncated at the "
            "token budget and retried without it"
        )
    return lines


def render_run_summary(
    *,
    total_seconds: float,
    analysis_seconds: float,
    generation_seconds: float,
    eligible: int,
    deeply_analyzed: int,
    planned: int,
    counters: LLMCounters | None,
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
        f"    analysis + selection   {_clock(analysis_seconds):>8}   "
        f"{deeply_analyzed} of {eligible} deeply analyzed, {planned} planned",
        f"    generation             {_clock(generation_seconds):>8}",
    ]
    lines.extend(_llm_lines(counters))
    return "\n".join(lines)


def render_llm_totals(metrics: dict) -> str:
    """The model's bill from a stored run, phrased exactly as the run block did.

    `runs show` and `auto status` read this from the database; the end-of-run
    block computes it live. Both go through the same renderer so the same run
    cannot be described two different ways on two surfaces.
    """
    from immich_memories.analysis.llm_metrics import LLMCounters

    counters = LLMCounters(
        calls=int(metrics.get("llm_calls", 0)),
        cache_hits=int(metrics.get("llm_cache_hits", 0)),
        prompt_tokens=int(metrics.get("llm_prompt_tokens", 0)),
        completion_tokens=int(metrics.get("llm_completion_tokens", 0)),
        truncated=int(metrics.get("llm_truncated", 0)),
        wall_seconds=float(metrics.get("llm_wall_seconds", 0.0)),
    )
    return "\n".join(line for line in _llm_lines(counters) if line).strip()
