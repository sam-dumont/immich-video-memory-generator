"""Tests for sidebar step completion logic."""

from __future__ import annotations

import pytest

from immich_memories.ui.app import _is_step_complete
from immich_memories.ui.state import AppState


class TestIsStepComplete:
    """Verify step completion checks against AppState fields."""

    def test_step1_incomplete_when_no_config(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 1) is False

    def test_step1_incomplete_when_config_but_no_date_range(self) -> None:
        state = AppState()
        state.config = object()  # type: ignore[assignment]
        assert _is_step_complete(state, 1) is False

    def test_step1_complete_when_config_and_date_range_set(self) -> None:
        state = AppState()
        state.config = object()  # type: ignore[assignment]
        state.date_range = object()  # type: ignore[assignment]
        assert _is_step_complete(state, 1) is True

    def test_step2_incomplete_when_no_clips_selected(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 2) is False

    def test_step2_complete_when_clips_selected(self) -> None:
        state = AppState()
        state.selected_clip_ids = {"clip-1", "clip-2"}
        assert _is_step_complete(state, 2) is True

    def test_step3_incomplete_when_no_options(self) -> None:
        state = AppState()
        assert _is_step_complete(state, 3) is False

    def test_step3_complete_when_options_set(self) -> None:
        state = AppState()
        state.generation_options = {"resolution": "1080p"}
        assert _is_step_complete(state, 3) is True

    @pytest.mark.parametrize("step", [4, 5, 0, -1])
    def test_other_steps_always_incomplete(self, step: int) -> None:
        state = AppState()
        assert _is_step_complete(state, step) is False
