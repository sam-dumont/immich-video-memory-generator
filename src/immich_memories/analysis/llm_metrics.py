"""What a run spent on the LLM, counted where the answers arrive.

The truncation counter is why this exists. `_query_openai` has always noticed
`finish_reason == "length"` on a thinking call, logged a warning and retried
without thinking -- and that is all. A silent, correct retry is exactly the
shape of problem that hides: #600's truncation tax sat in an unread server log
for months while every run paid it.

A ContextVar rather than a threaded argument, for the reason
`selection_trace` gives: `query_llm` is called from the CLI, the wizard, the
auto runner and scripts, and giving all of them a counters parameter to carry
data none of them read would obscure the code being measured. It is
task-local, not global -- but it does not cross a thread-pool boundary, so
`collecting()` must be entered *inside* the run, where `tracing()` is.

Recording is a no-op when nothing is collecting, so a probe or a one-off
script pays nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_active: ContextVar[LLMCounters | None] = ContextVar("llm_counters", default=None)

__all__ = ["LLMCounters", "collecting", "record_cache_hit", "record_reply", "record_wall"]


@dataclass
class LLMCounters:
    """What the model was asked, and what it cost."""

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    truncated: int = 0
    wall_seconds: float = 0.0

    def as_metrics(self) -> dict[str, float | int]:
        """The subset worth persisting, omitting whatever stayed zero.

        A phase that made no LLM calls should carry no LLM keys at all, so a
        reader can tell "did not use the model" from "used it and it was free".

        Fields are listed explicitly rather than walked with `vars()`: a
        dynamic read is invisible to Vulture, which then reports every counter
        as unused.
        """
        candidates = {
            "llm_calls": self.calls,
            "llm_cache_hits": self.cache_hits,
            "llm_prompt_tokens": self.prompt_tokens,
            "llm_completion_tokens": self.completion_tokens,
            "llm_truncated": self.truncated,
            "llm_wall_seconds": round(self.wall_seconds, 3),
        }
        return {name: value for name, value in candidates.items() if value}

    def since(self, mark: LLMCounters) -> LLMCounters:
        """What has been spent since `mark` was taken."""
        return LLMCounters(
            calls=self.calls - mark.calls,
            cache_hits=self.cache_hits - mark.cache_hits,
            prompt_tokens=self.prompt_tokens - mark.prompt_tokens,
            completion_tokens=self.completion_tokens - mark.completion_tokens,
            truncated=self.truncated - mark.truncated,
            wall_seconds=self.wall_seconds - mark.wall_seconds,
        )

    def snapshot(self) -> LLMCounters:
        """A frozen copy, so a later `since` can measure against this moment."""
        return LLMCounters(
            calls=self.calls,
            cache_hits=self.cache_hits,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            truncated=self.truncated,
            wall_seconds=self.wall_seconds,
        )


def record_reply(*, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """One reply arrived from the model. Retries count separately, as they cost."""
    counters = _active.get()
    if counters is None:
        return
    counters.calls += 1
    counters.prompt_tokens += prompt_tokens
    counters.completion_tokens += completion_tokens


def record_truncation() -> None:
    """A thinking call hit the token budget and its reasoning was discarded."""
    counters = _active.get()
    if counters is not None:
        counters.truncated += 1


def record_cache_hit() -> None:
    """An identical question was answered from the judgment cache, unpaid for."""
    counters = _active.get()
    if counters is not None:
        counters.cache_hits += 1


def record_wall(seconds: float) -> None:
    counters = _active.get()
    if counters is not None:
        counters.wall_seconds += seconds


def active() -> LLMCounters | None:
    return _active.get()


@contextmanager
def collecting() -> Iterator[LLMCounters]:
    """Count LLM spend for the duration of a run."""
    counters = LLMCounters()
    token: Token[LLMCounters | None] = _active.set(counters)
    try:
        yield counters
    finally:
        _active.reset(token)
