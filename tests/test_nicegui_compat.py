"""Contracts for NiceGUI background-task compatibility."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from immich_memories.ui.nicegui_compat import (
    io_bound_result,
    is_client_disconnect_error,
    run_ui_observer,
)


@pytest.mark.asyncio
async def test_io_bound_result_returns_callback_value() -> None:
    with patch(
        "immich_memories.ui.nicegui_compat.run.io_bound",
        new=AsyncMock(return_value=42),
    ):
        assert await io_bound_result(lambda: 42) == 42


@pytest.mark.asyncio
async def test_io_bound_result_normalizes_nicegui_shutdown_to_cancellation() -> None:
    with (
        patch(
            "immich_memories.ui.nicegui_compat.run.io_bound",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await io_bound_result(lambda: 42)


@pytest.mark.asyncio
async def test_io_bound_result_preserves_non_cancellation_errors() -> None:
    failure = RuntimeError("worker failed")
    with (
        patch(
            "immich_memories.ui.nicegui_compat.run.io_bound",
            new=AsyncMock(side_effect=failure),
        ),
        pytest.raises(RuntimeError, match="worker failed") as raised,
    ):
        await io_bound_result(lambda: 42)

    assert raised.value is failure


@pytest.mark.parametrize(
    "message",
    [
        "The client this element belongs to has been deleted.",
        "The client this outbox belongs to has been deleted.",
    ],
)
def test_ui_observer_suppresses_only_known_nicegui_disconnects(message: str) -> None:
    failure = RuntimeError(message)

    completed = run_ui_observer(
        lambda: (_ for _ in ()).throw(failure),
        description="test observer",
    )

    assert completed is False
    assert is_client_disconnect_error(failure)


@pytest.mark.parametrize(
    "message",
    [
        "worker failed",
        "The element this style object belongs to has been deleted.",
        "The parent element this slot belongs to has been deleted.",
    ],
)
def test_ui_observer_preserves_unrelated_runtime_errors(message: str) -> None:
    failure = RuntimeError(message)

    with pytest.raises(RuntimeError) as raised:
        run_ui_observer(
            lambda: (_ for _ in ()).throw(failure),
            description="test observer",
        )

    assert raised.value is failure
    assert not is_client_disconnect_error(failure)
