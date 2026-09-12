"""Cooperative cancellation at request boundaries, shared by UI and planning."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class PipelineCancelled(BaseException):
    """A user stop, never a model failure that an editorial fallback may consume.

    Like asyncio.CancelledError, this bypasses broad Exception handlers. The run
    boundary catches it explicitly and records cancellation before cleanup.
    """


_CHECK: ContextVar[Callable[[], None] | None] = ContextVar("planning_cancel_check", default=None)


@contextmanager
def cancellation_scope(check: Callable[[], None]) -> Iterator[None]:
    """Install a run-local check; nested and concurrent runs keep separate signals."""
    token = _CHECK.set(check)
    try:
        check_cancelled()
        yield
    finally:
        _CHECK.reset(token)


def check_cancelled() -> None:
    """Finish the current request, then stop before paying for another one."""
    check = _CHECK.get()
    if check is not None:
        check()
