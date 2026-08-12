"""Contracts for NiceGUI background-task compatibility."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from immich_memories.ui.nicegui_compat import io_bound_result


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
