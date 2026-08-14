"""Behavior tests for Step 2 Auto/manual duration controls."""

from __future__ import annotations

from immich_memories.ui.pages.step2_review import (
    _duration_mode_label,
    _set_manual_target_minutes,
)
from immich_memories.ui.state import AppState


def test_auto_duration_label_shows_the_resolved_runtime() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    assert _duration_mode_label(state) == "Auto · 2m 30s"


def test_editing_duration_switches_to_manual_without_rounding() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    _set_manual_target_minutes(state, 3.25)

    assert state.duration_mode == "manual"
    assert state.target_duration == 3.25
    assert state.target_duration_seconds == 195.0
