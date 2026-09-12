"""Resolve editorial async work from synchronous CLI and UI callers."""

from __future__ import annotations

import asyncio
import threading
from contextvars import copy_context
from typing import Any


def _run_sync(awaitable: Any) -> Any:
    """Resolve one async editor from synchronous CLI/UI planning code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller.
            errors.append(exc)

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(runner), daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    if not result:
        raise RuntimeError("editorial async bridge returned no result")
    return result[0]
