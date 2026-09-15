"""`runs show` and `auto status` report the same spend the run block did.

Both read the run database. Neither invents phases it does not hold: the
phase table shows the three phases the tracker records, and the model's bill
is reported at run level, which is where it is stored and why.
"""

from __future__ import annotations

from immich_memories.analysis.llm_metrics import LLMCounters
from immich_memories.cli._run_summary import render_llm_totals, render_run_summary


def test_overlapping_request_time_is_named_separately_from_elapsed_time() -> None:
    counters = LLMCounters(calls=4, wall_seconds=120.0)
    live = render_run_summary(
        total_seconds=40.0,
        analysis_seconds=30.0,
        generation_seconds=10.0,
        eligible=20,
        planned=8,
        counters=counters,
    )
    stored = render_llm_totals(counters.as_metrics())

    assert "Memory generated in 40s" in live
    assert "2m 00s summed request time" in live
    assert "2m 00s summed request time" in stored


def test_reasoning_is_visible_as_part_of_completion_in_live_and_stored_totals() -> None:
    counters = LLMCounters(calls=2, completion_tokens=1600, reasoning_tokens=1200)
    live = render_run_summary(
        total_seconds=40.0,
        analysis_seconds=30.0,
        generation_seconds=10.0,
        eligible=20,
        planned=8,
        counters=counters,
    )
    stored = render_llm_totals(counters.as_metrics())

    for text in (live, stored):
        assert "1.6k completion" in text
        assert "1.2k of the completion tokens were reasoning" in text


def test_the_totals_line_names_the_cache_it_counted() -> None:
    """Three caches exist in this tool; the line has to say which one it means."""
    line = render_llm_totals({"llm_calls": 11, "llm_cache_hits": 4, "llm_wall_seconds": 138.0})

    assert "11 calls" in line
    assert "judgment cache" in line


def test_a_truncation_is_reported_after_the_fact_too() -> None:
    """The run block says it live; `runs show` has to still say it tomorrow."""
    line = render_llm_totals({"llm_calls": 9, "llm_truncated": 2})

    assert "2 thinking calls truncated" in line


def test_a_run_with_no_model_spend_renders_nothing() -> None:
    assert render_llm_totals({}) == ""
