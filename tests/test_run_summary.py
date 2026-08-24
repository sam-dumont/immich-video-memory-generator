"""The block a run prints about itself.

Granularity is the design constraint, not a nicety. The CLI measures analysis
and selection live because it wraps those calls; the run database does not
record them as phases at all. The block says which is which, so a partial
picture never reads as the whole one.
"""

from __future__ import annotations

from immich_memories.analysis.llm_metrics import LLMCounters
from immich_memories.cli._run_summary import render_run_summary


def test_the_block_reports_what_it_measured_this_run() -> None:
    text = render_run_summary(
        total_seconds=387.0,
        analysis_seconds=229.0,
        generation_seconds=158.0,
        eligible=312,
        deeply_analyzed=28,
        planned=14,
        counters=LLMCounters(),
    )

    assert "6m 27s" in text
    assert "measured this run" in text
    assert "28 of 312" in text
    assert "14 planned" in text


def test_a_truncated_thinking_call_is_named_in_the_block() -> None:
    """The sentence that would have surfaced #600 on the night it started."""
    counters = LLMCounters(calls=11, cache_hits=4, truncated=1, wall_seconds=138.0)

    text = render_run_summary(
        total_seconds=387.0,
        analysis_seconds=229.0,
        generation_seconds=158.0,
        eligible=312,
        deeply_analyzed=28,
        planned=14,
        counters=counters,
    )

    assert "11 calls" in text
    assert "truncated" in text
    assert "judgment cache" in text


def test_a_run_that_never_touched_the_model_prints_no_llm_line() -> None:
    """A NAS run with no LLM configured should not read as a broken one."""
    text = render_run_summary(
        total_seconds=90.0,
        analysis_seconds=40.0,
        generation_seconds=50.0,
        eligible=20,
        deeply_analyzed=20,
        planned=8,
        counters=LLMCounters(),
    )

    assert "LLM" not in text


def test_a_healthy_llm_run_stays_quiet_about_truncation() -> None:
    counters = LLMCounters(calls=6, cache_hits=2, wall_seconds=44.0)

    text = render_run_summary(
        total_seconds=120.0,
        analysis_seconds=70.0,
        generation_seconds=50.0,
        eligible=40,
        deeply_analyzed=12,
        planned=9,
        counters=counters,
    )

    assert "6 calls" in text
    assert "truncated" not in text
