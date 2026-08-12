"""Compatibility helpers for NiceGUI background work."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from nicegui import run

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)

_NICEGUI_CLIENT_DISCONNECT_MESSAGES = frozenset(
    {
        "The client this element belongs to has been deleted.",
        "The client this outbox belongs to has been deleted.",
    }
)


def is_client_disconnect_error(error: BaseException) -> bool:
    """Recognize NiceGUI's exact deleted-client write failures."""
    return isinstance(error, RuntimeError) and str(error) in _NICEGUI_CLIENT_DISCONNECT_MESSAGES


def run_ui_observer(callback: Callable[[], object], *, description: str) -> bool:
    """Run one UI-only write, suppressing only a deleted NiceGUI client."""
    try:
        callback()
    except RuntimeError as exc:
        if not is_client_disconnect_error(exc):
            raise
        logger.debug("Skipped %s because the NiceGUI client disconnected", description)
        return False
    return True


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
