"""Compatibility helpers for NiceGUI background work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from nicegui import run

P = ParamSpec("P")
R = TypeVar("R")


async def io_bound_result(callback: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run a callback which promises a result, normalizing NiceGUI cancellation.

    NiceGUI 3.16 returns ``None`` when the await is cancelled or the app shuts
    down. NiceGUI 4 will raise ``CancelledError`` instead, so expose that future
    contract to callers now.
    """
    result = await run.io_bound(callback, *args, **kwargs)
    if result is None:
        raise asyncio.CancelledError
    return result
