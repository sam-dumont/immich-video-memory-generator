"""Bounded recovery for a judge answer whose envelope the asking stage reads itself.

The text gateway recovers what it can see: a truncated transport, or a reply that is not
one complete JSON object when the stage declared that contract. A stage that reads its
own envelope -- the period-account pages, the moment inventory -- only learns the answer
is unusable after the gateway has already banked it, and a raw json.JSONDecodeError used
to travel from there to the CLI. Measured 2026-09-14 on a local 35B: three keys lost
their opening quote, an hour-long read ended on "Expecting property name enclosed in
double quotes", and calls/ held no record naming the stage that produced it.

Nothing here repairs a malformed answer. A model that cannot write JSON has failed, and
that result is the point of measuring it; what this bounds is the cost of finding out and
the legibility of the ending.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

PAGE_READ_SCHEMA = "bounded-page-read-v1"

# "The model wrote something no decoder can read" and "the model wrote readable JSON
# that does not answer the question" are different findings about a reader. The moment
# inventory raises both from one call (unreadable page, or a page that left sources
# unaccounted for), so the record has to say which.
UNREADABLE = "unreadable_json"
CONTRACT_NOT_MET = "contract_not_met"

T = TypeVar("T")


def failure_kind(error: ValueError) -> str:
    """Which of the two a reader's failure was, for a record the matrix reads."""
    return UNREADABLE if isinstance(error, json.JSONDecodeError) else CONTRACT_NOT_MET


class PageReadFailure(ValueError):
    """A stage's own envelope contract failed every bounded attempt.

    Carries the whole attempt trail so a run can report "the reader answered unreadable
    JSON at story-episodes-3 after three calls" instead of a decoder message.
    """

    def __init__(self, stage: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(
            f"editorial source evidence unavailable: {stage} answer could not be read after "
            f"{len(attempts)} attempts: {attempts[-1]['error']}"
        )
        self.stage = stage
        self.attempts = attempts

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": PAGE_READ_SCHEMA,
            "stage": self.stage,
            "attempt_count": len(self.attempts),
            "failure_kind": self.attempts[-1]["failure_kind"],
            "error": self.attempts[-1]["error"],
            "attempts": self.attempts,
        }


def record_page_failure(judge, stage: str, attempts: list[dict[str, Any]]) -> PageReadFailure:
    """Keep the exhausted attempts beside the run's calls and return the error to raise."""
    failure = PageReadFailure(stage, attempts)
    judge.record_failure(stage, failure.as_record())
    return failure


def _repair_request(prompt: str, error: str) -> str:
    """The same question, with the reason the previous answer was unreadable named in it."""
    return prompt + (
        f"\n\nThe previous answer could not be read: {error}. "
        "Answer the same question again as ONE complete JSON object. Put every property name "
        "in double quotes, separate properties with commas, leave no comma before a closing "
        "brace or bracket, and write nothing after the object."
    )


def read_page_answer(
    judge,
    *,
    stage: str,
    prompt: str,
    max_tokens: int,
    read: Callable[[str], T],
) -> T:
    """Ask, retry once on a doubled budget, then repair once with the problem named.

    `read` raises ValueError -- json.JSONDecodeError is one -- when it cannot read the
    answer. The first round keeps the original prompt bytes and budget, so a banked run
    replays unchanged and no digest moves; each later round carries its own judgment key.
    """
    attempts: list[dict[str, Any]] = []
    rounds = (
        (stage, max_tokens, False),
        (f"{stage}-retry", max_tokens * 2, False),
        (f"{stage}-repair", max_tokens * 2, True),
    )
    for asked, budget, repair in rounds:
        question = _repair_request(prompt, attempts[-1]["error"]) if repair else prompt
        raw = judge.ask(asked, question, max_tokens=budget)
        try:
            return read(raw)
        except ValueError as exc:
            attempts.append(
                {
                    "stage": asked,
                    "max_tokens": budget,
                    "failure_kind": failure_kind(exc),
                    "error": str(exc),
                    "raw": raw,
                }
            )
    raise record_page_failure(judge, stage, attempts)
