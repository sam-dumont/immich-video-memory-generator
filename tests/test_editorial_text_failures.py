"""A bounded completion failure replays only when it is provably the same failure."""

from __future__ import annotations

import pytest

from immich_memories.analysis.editorial_text_failures import (
    FAILURE_SCHEMA,
    TextCompletionFailure,
)


def attempt(**overrides) -> dict:
    return {
        "outcome": "incomplete",
        "raw": '{"threads":[',
        "max_tokens": 2500,
        "error": "response ended mid-object",
        **overrides,
    }


def test_a_recorded_failure_replays_as_the_same_failure_marked_as_a_replay() -> None:
    """The second run must not re-pay for an exhaustion the first run already proved."""
    original = TextCompletionFailure([attempt(), attempt(max_tokens=5000)])

    replayed = TextCompletionFailure.from_record(original.as_record())

    assert replayed is not None
    assert replayed.attempts == original.attempts
    assert replayed.cache_hit and not original.cache_hit


def test_the_failure_says_which_error_ended_the_last_attempt() -> None:
    """Callers surface this text as the reason a stage fell back."""
    failure = TextCompletionFailure(
        [attempt(), attempt(max_tokens=5000, error="second attempt was truncated too")]
    )

    assert "second attempt was truncated too" in str(failure)


@pytest.mark.parametrize(
    "record",
    [
        None,
        "not a record",
        {"attempts": [attempt(), attempt()]},
        {"schema_version": "bounded-text-completion-v0", "attempts": [attempt(), attempt()]},
        {"schema_version": FAILURE_SCHEMA, "attempts": attempt()},
        {"schema_version": FAILURE_SCHEMA, "attempts": [attempt()]},
        {"schema_version": FAILURE_SCHEMA, "attempts": [attempt()] * 3},
    ],
)
def test_a_record_from_another_contract_is_never_replayed(record) -> None:
    assert TextCompletionFailure.from_record(record) is None


@pytest.mark.parametrize(
    "row",
    [
        "not an attempt",
        attempt(outcome="network_error"),
        attempt(raw=None),
        attempt(max_tokens="2500"),
        attempt(max_tokens=0),
        attempt(error=""),
        attempt(error=None),
        {key: value for key, value in attempt().items() if key != "raw"},
        attempt(unexpected="field"),
    ],
)
def test_an_attempt_that_cannot_be_verified_voids_the_whole_replay(row) -> None:
    """A partially trustworthy record would freeze a failure nobody can reproduce."""
    record = {"schema_version": FAILURE_SCHEMA, "attempts": [attempt(), row]}

    assert TextCompletionFailure.from_record(record) is None
