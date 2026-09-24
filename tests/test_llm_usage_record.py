"""The file a bill is added up from, as opposed to the line a person reads.

`_run_summary` renders `115.6k prompt` because six digits in a terminal help
nobody. A report that multiplies tokens by a price needs the six digits, and
rounding a hosted month's prompt tokens to the nearest hundred is real money.
"""

from __future__ import annotations

import json

from immich_memories.analysis.llm_metrics import LLMCounters, ModelSpend
from immich_memories.analysis.llm_usage_record import USAGE_FILE, write_llm_usage


def _counters() -> LLMCounters:
    return LLMCounters(
        calls=93,
        cache_hits=1,
        prompt_tokens=115_592,
        cached_prompt_tokens=11_904,
        completion_tokens=15_788,
        reasoning_tokens=13_469,
        truncated=2,
        wall_seconds=325.6494,
        by_model={"glm-5.3-flash": ModelSpend(calls=93, prompt_tokens=115_592)},
    )


def test_the_record_keeps_every_digit_the_run_counted(tmp_path) -> None:
    write_llm_usage(tmp_path, _counters())

    record = json.loads((tmp_path / USAGE_FILE).read_text())

    assert record["calls"] == 93
    assert record["cache_hits"] == 1
    assert record["prompt_tokens"] == 115_592
    assert record["cached_prompt_tokens"] == 11_904
    assert record["completion_tokens"] == 15_788
    assert record["reasoning_tokens"] == 13_469
    assert record["truncated"] == 2
    assert record["wall_seconds"] == 325.649


def test_the_record_says_which_model_was_billed(tmp_path) -> None:
    write_llm_usage(tmp_path, _counters())

    by_model = json.loads((tmp_path / USAGE_FILE).read_text())["by_model"]

    assert by_model["glm-5.3-flash"]["calls"] == 93
    assert by_model["glm-5.3-flash"]["prompt_tokens"] == 115_592


def test_a_run_that_never_asked_a_model_leaves_no_record(tmp_path) -> None:
    """Absent has to read as "no model", not as a bill of zero."""
    write_llm_usage(tmp_path, LLMCounters())
    write_llm_usage(tmp_path, None)

    assert not (tmp_path / USAGE_FILE).exists()


def test_a_cache_only_run_still_leaves_a_record(tmp_path) -> None:
    """Every answer reused is a call the run did not pay for; that is a result."""
    write_llm_usage(tmp_path, LLMCounters(cache_hits=7))

    assert json.loads((tmp_path / USAGE_FILE).read_text())["cache_hits"] == 7


def test_an_unwritable_directory_does_not_fail_a_finished_run(tmp_path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    write_llm_usage(blocked, _counters())
    assert blocked.read_text() == "occupied"


def test_failed_checkpoint_replacement_preserves_the_previous_usage(tmp_path, monkeypatch):
    import os

    write_llm_usage(tmp_path, LLMCounters(calls=1, prompt_tokens=50))

    def unavailable_replace(*args):
        raise OSError("disk cannot replace checkpoint")

    # WHY: fail the filesystem's atomic commit, preserving the last durable charge total.
    monkeypatch.setattr(os, "replace", unavailable_replace)
    write_llm_usage(tmp_path, LLMCounters(calls=2, prompt_tokens=100))

    assert json.loads((tmp_path / USAGE_FILE).read_text())["prompt_tokens"] == 50
    assert sorted(p.name for p in tmp_path.iterdir()) == [USAGE_FILE]


def test_a_batch_total_with_unmetered_lines_is_not_presented_as_complete(tmp_path) -> None:
    counters = LLMCounters(
        calls=3,
        unmetered_calls=1,
        batch_calls=2,
        batch_unmetered_calls=1,
        batch_prompt_tokens=10,
    )
    write_llm_usage(tmp_path, counters)

    record = json.loads((tmp_path / USAGE_FILE).read_text())

    assert record["usage_complete"] is False
    assert record["batch_unmetered_calls"] == 1
    assert record["batch_usage_complete"] is False
