"""How long a repeatedly-failing candidate stays out of the running.

One failure is noise -- a transient Immich hiccup, a stalled LLM. A candidate
that fails twice in a row is usually broken in a way tonight will not fix, and
without a backoff it wins the selection again every night: a real log shows the
same monthly candidate launched nine nights running, each time consuming the
whole nightly slot and producing nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.automation.failure_backoff import suppressed_keys
from immich_memories.automation.state_store import FailureStreak

NOW = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)


def _streak(count: int, hours_ago: float) -> FailureStreak:
    return FailureStreak(count=count, last_failed_at=NOW - timedelta(hours=hours_ago))


def test_a_single_failure_is_not_held_against_a_candidate():
    """Transient failures happen; one is not evidence of a broken memory."""
    assert suppressed_keys({"a": _streak(1, hours_ago=1)}, NOW) == {}


def test_two_failures_suppress_for_a_day():
    assert "a" in suppressed_keys({"a": _streak(2, hours_ago=1)}, NOW)


def test_the_window_expires_so_a_candidate_gets_another_chance():
    """Backoff delays a retry; it must never be permanent."""
    assert suppressed_keys({"a": _streak(2, hours_ago=25)}, NOW) == {}


def test_more_failures_back_off_for_longer():
    """Still suppressed at 25h, where a two-failure streak would have expired."""
    assert "a" in suppressed_keys({"a": _streak(3, hours_ago=25)}, NOW)


def test_backoff_is_capped_so_a_candidate_returns_within_a_week():
    assert "a" in suppressed_keys({"a": _streak(9, hours_ago=24 * 6)}, NOW)
    assert suppressed_keys({"a": _streak(9, hours_ago=24 * 8)}, NOW) == {}


def test_the_reason_says_how_many_times_it_failed():
    """The operator has to be able to tell this from 'no candidates found'."""
    reason = suppressed_keys({"a": _streak(3, hours_ago=1)}, NOW)["a"]

    assert "3" in reason


def test_a_streak_with_no_recorded_time_is_not_suppressed():
    """Never strand a candidate on incomplete data."""
    assert suppressed_keys({"a": FailureStreak(count=5, last_failed_at=None)}, NOW) == {}
