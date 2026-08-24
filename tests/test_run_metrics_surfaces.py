"""`runs show` and `auto status` report the same spend the run block did.

Both read the run database. Neither invents phases it does not hold: the
phase table shows the three phases the tracker records, and the model's bill
is reported at run level, which is where it is stored and why.
"""

from __future__ import annotations

from immich_memories.cli._run_summary import render_llm_totals


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
