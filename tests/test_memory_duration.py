"""The duration line: the type's own answer, or an override that keeps its seconds."""

from __future__ import annotations

from immich_memories.ui.pages.memory_duration import (
    auto_duration_note,
    duration_label,
    set_auto,
    set_manual_minutes,
)
from immich_memories.ui.state import AppState


def test_auto_label_shows_the_resolved_runtime() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    assert duration_label(state) == "Auto · 2m 30s"


def test_manual_label_names_the_override() -> None:
    state = AppState(duration_mode="manual", target_duration=1.25)

    assert duration_label(state) == "Manual · 1m 15s"


def test_an_override_keeps_fractional_minutes_exact() -> None:
    state = AppState(duration_mode="auto", target_duration=2.5)

    set_manual_minutes(state, 3.25)

    assert state.duration_mode == "manual"
    assert state.target_duration_seconds == 195.0


def test_going_back_to_auto_keeps_the_last_resolved_value() -> None:
    state = AppState(duration_mode="manual", target_duration=3.25)

    set_auto(state)

    assert state.duration_mode == "auto"
    assert state.target_duration == 3.25


def test_types_that_measure_their_media_say_so() -> None:
    assert "media" in auto_duration_note("trip")
    assert "media" in auto_duration_note("album")
    assert "default" in auto_duration_note("year_in_review")
