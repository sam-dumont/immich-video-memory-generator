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
from dataclasses import dataclass, field
from functools import wraps
from threading import Lock
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_P = ParamSpec("_P")
_T = TypeVar("_T")

_active: ContextVar[tuple[LLMCounters, ...]] = ContextVar("llm_counters", default=())

__all__ = [
    "LLMCounters",
    "ModelSpend",
    "collecting",
    "counted",
    "record_batch_reply",
    "record_cache_hit",
    "record_reply",
    "record_wall",
]


@dataclass
class ModelSpend:
    """One model's share of a run, for the runs that ask more than one."""

    calls: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class LLMCounters:
    """What the model was asked, and what it cost.

    `by_model` is a run-level tally and `since`/`snapshot` do not carry it: a
    phase delta is read as totals, and attributing a delta per model would be
    code with no reader.
    """

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    # A subset of completion_tokens, which is how every provider in this tree
    # reports it -- and the subset that surprises: 13,469 reasoning tokens came
    # back on one 5.4k-token prompt, billed at the completion rate.
    reasoning_tokens: int = 0
    truncated: int = 0
    # Sum of individual request durations, not elapsed time when requests overlap.
    # Keep the historical field name so stored run metrics remain readable.
    wall_seconds: float = 0.0
    by_model: dict[str, ModelSpend] = field(default_factory=dict)
    # The subset of the three counters above that a provider batch answered
    # rather than a live call. A subset rather than a separate tally, so every
    # existing reader of `calls` still sees every reply the run paid for; what
    # a batch changes is the price, not whether the question was asked.
    batch_calls: int = 0
    batch_prompt_tokens: int = 0
    batch_completion_tokens: int = 0
    batch_reasoning_tokens: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def as_metrics(self) -> dict[str, float | int]:
        """The subset worth persisting, omitting whatever stayed zero.

        A phase that made no LLM calls should carry no LLM keys at all, so a
        reader can tell "did not use the model" from "used it and it was free".

        Fields are listed explicitly rather than walked with `vars()`: a
        dynamic read is invisible to Vulture, which then reports every counter
        as unused.
        """
        with self._lock:
            candidates = {
                "llm_calls": self.calls,
                "llm_cache_hits": self.cache_hits,
                "llm_prompt_tokens": self.prompt_tokens,
                "llm_cached_prompt_tokens": self.cached_prompt_tokens,
                "llm_completion_tokens": self.completion_tokens,
                "llm_reasoning_tokens": self.reasoning_tokens,
                "llm_truncated": self.truncated,
                "llm_wall_seconds": round(self.wall_seconds, 3),
                "llm_batch_calls": self.batch_calls,
                "llm_batch_prompt_tokens": self.batch_prompt_tokens,
                "llm_batch_completion_tokens": self.batch_completion_tokens,
                "llm_batch_reasoning_tokens": self.batch_reasoning_tokens,
            }
        return {name: value for name, value in candidates.items() if value}

    def since(self, mark: LLMCounters) -> LLMCounters:
        """What has been spent since `mark` was taken."""
        with self._lock:
            return LLMCounters(
                calls=self.calls - mark.calls,
                cache_hits=self.cache_hits - mark.cache_hits,
                prompt_tokens=self.prompt_tokens - mark.prompt_tokens,
                cached_prompt_tokens=self.cached_prompt_tokens - mark.cached_prompt_tokens,
                completion_tokens=self.completion_tokens - mark.completion_tokens,
                reasoning_tokens=self.reasoning_tokens - mark.reasoning_tokens,
                truncated=self.truncated - mark.truncated,
                wall_seconds=self.wall_seconds - mark.wall_seconds,
                batch_calls=self.batch_calls - mark.batch_calls,
                batch_prompt_tokens=self.batch_prompt_tokens - mark.batch_prompt_tokens,
                batch_completion_tokens=(
                    self.batch_completion_tokens - mark.batch_completion_tokens
                ),
                batch_reasoning_tokens=self.batch_reasoning_tokens - mark.batch_reasoning_tokens,
            )

    def snapshot(self) -> LLMCounters:
        """A frozen copy, so a later `since` can measure against this moment."""
        with self._lock:
            return LLMCounters(
                calls=self.calls,
                cache_hits=self.cache_hits,
                prompt_tokens=self.prompt_tokens,
                cached_prompt_tokens=self.cached_prompt_tokens,
                completion_tokens=self.completion_tokens,
                reasoning_tokens=self.reasoning_tokens,
                truncated=self.truncated,
                wall_seconds=self.wall_seconds,
                batch_calls=self.batch_calls,
                batch_prompt_tokens=self.batch_prompt_tokens,
                batch_completion_tokens=self.batch_completion_tokens,
                batch_reasoning_tokens=self.batch_reasoning_tokens,
            )


def record_reply(
    *,
    prompt_tokens: int = 0,
    cached_prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    model: str | None = None,
) -> None:
    """One reply arrived from the model. Retries count separately, as they cost.

    `model` is whatever the server named in its own reply, not what the config
    asked for: a route that silently serves something else bills for what it
    served.
    """
    for counters in _active.get():
        with counters._lock:
            counters.calls += 1
            counters.prompt_tokens += prompt_tokens
            counters.cached_prompt_tokens += cached_prompt_tokens
            counters.completion_tokens += completion_tokens
            counters.reasoning_tokens += reasoning_tokens
            if model:
                spend = counters.by_model.setdefault(model, ModelSpend())
                spend.calls += 1
                spend.prompt_tokens += prompt_tokens
                spend.cached_prompt_tokens += cached_prompt_tokens
                spend.completion_tokens += completion_tokens
                spend.reasoning_tokens += reasoning_tokens


def record_batch_reply(
    *, prompt_tokens: int = 0, completion_tokens: int = 0, reasoning_tokens: int = 0
) -> None:
    """One reply arrived from a provider batch rather than a live call."""
    for counters in _active.get():
        with counters._lock:
            counters.calls += 1
            counters.batch_calls += 1
            counters.prompt_tokens += prompt_tokens
            counters.batch_prompt_tokens += prompt_tokens
            counters.completion_tokens += completion_tokens
            counters.batch_completion_tokens += completion_tokens
            counters.reasoning_tokens += reasoning_tokens
            counters.batch_reasoning_tokens += reasoning_tokens


def record_truncation() -> None:
    """A thinking call hit the token budget and its reasoning was discarded."""
    for counters in _active.get():
        with counters._lock:
            counters.truncated += 1


def record_cache_hit() -> None:
    """An identical question was answered from the judgment cache, unpaid for."""
    for counters in _active.get():
        with counters._lock:
            counters.cache_hits += 1


def record_wall(seconds: float) -> None:
    for counters in _active.get():
        with counters._lock:
            counters.wall_seconds += seconds


def active() -> LLMCounters | None:
    """The run's own collector -- the outermost -- or None when nothing is counting.

    Outermost rather than innermost because every caller of this asks a
    run-level question: what has this run spent so far. A stage that wants its
    own share opens `collecting()` and reads what it yields.
    """
    stack = _active.get()
    return stack[0] if stack else None


@contextmanager
def collecting() -> Iterator[LLMCounters]:
    """Count LLM spend for the duration of this block, and of every block outside it.

    A stack rather than a single holder, because a nested scope used to
    *replace* the enclosing one: `plan_structure` opens its own collector so
    the plan record can say what selection cost, and that silently emptied the
    run's bill for the 90 calls selection made. Every open collector now
    receives every reply, so measuring a stage can never cost the run its
    total.
    """
    counters = LLMCounters()
    token: Token[tuple[LLMCounters, ...]] = _active.set((*_active.get(), counters))
    try:
        yield counters
    finally:
        _active.reset(token)


def counted(run: Callable[_P, _T]) -> Callable[_P, _T]:
    """Count everything one whole run asks a model, so the run can report its bill.

    A decorator and not a `with` inside the body: the collector has to span
    selection and generation both, which is the entire body of
    `run_pipeline_and_generate`, and putting three hundred lines of the code
    being measured inside an extra indent to measure them is the wrong trade.
    """

    @wraps(run)
    def counting(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        with collecting():
            return run(*args, **kwargs)

    return counting
