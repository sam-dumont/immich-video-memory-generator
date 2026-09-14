"""What the Trip card's Year picker does when loading it goes wrong.

The picker is disabled while trips are fetched, so the only thing that can
un-strand it is the load coroutine itself. A disabled picker with no failure
message is unrecoverable without a page reload.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.memory_types.registry import MemoryType
from immich_memories.ui.state import AppState


def test_year_picker_is_re_enabled_when_the_trip_render_raises() -> None:
    from immich_memories.ui.pages import step1_presets

    state = AppState(memory_preset_params={"year": 2024})
    state.connected_user = "someone"
    # WHY: NiceGUI widgets need a live client slot; these two stand in for the
    # whole widget layer, and the column's clear() raises the way a client that
    # disconnected mid-render does.
    widgets = MagicMock()
    widgets.column.return_value.classes.return_value.clear.side_effect = RuntimeError("client gone")
    with (
        # WHY: the wizard reads its state through a per-session accessor.
        patch.object(step1_presets, "get_app_state", return_value=state),
        patch.object(step1_presets, "ui", widgets),
        patch.object(step1_presets, "im_card", MagicMock()),
    ):
        step1_presets.render_type_params(MemoryType.TRIP)
        year_select = widgets.select.return_value.classes.return_value
        load_trip_years = widgets.timer.call_args.args[1]

        assert year_select.disable.called
        with pytest.raises(RuntimeError):
            asyncio.run(load_trip_years())

    assert year_select.enable.called
