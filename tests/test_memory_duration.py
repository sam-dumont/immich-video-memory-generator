"""The duration line: the type's own answer, or an override that keeps its seconds."""

from __future__ import annotations

from immich_memories.planning.auto_duration import DURATION_FROM_MATERIAL, DurationDecision
from immich_memories.ui.pages.memory_duration import (
    auto_duration_explanation,
    auto_duration_note,
    duration_label,
    set_auto,
    set_manual_minutes,
    switch_to_manual,
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


def test_an_album_is_measured_the_way_a_trip_is() -> None:
    """Both surfaces fit an album with the trip curve once its media is loaded."""
    assert auto_duration_note("album") == auto_duration_note("trip")


def _fitted(state: AppState, seconds: float) -> AppState:
    """The state a cut leaves behind once Auto has fitted the pool."""
    state.duration_decision = DurationDecision(
        seconds, DURATION_FROM_MATERIAL, photographed_days=3, capacity_seconds=19.5
    )
    state.duration_decided_from = state.target_duration
    return state


def test_after_a_cut_auto_names_what_set_the_length() -> None:
    state = _fitted(AppState(duration_mode="auto", target_duration=1.0, memory_type="season"), 15)

    assert duration_label(state) == "Auto · 0m 15s"
    assert "the material: 3 photographed days" in auto_duration_explanation(state)


def test_before_a_cut_auto_says_where_the_length_will_come_from() -> None:
    state = AppState(duration_mode="auto", target_duration=1.0, memory_type="season")

    assert auto_duration_explanation(state) == auto_duration_note("season")


def test_a_new_card_drops_the_last_fit() -> None:
    state = _fitted(AppState(duration_mode="auto", target_duration=1.0), 15)

    state.target_duration = 10.0

    assert duration_label(state) == "Auto · 10m 00s"


def test_turning_auto_off_starts_the_override_from_the_fitted_length() -> None:
    state = _fitted(AppState(duration_mode="auto", target_duration=1.0), 15)

    switch_to_manual(state)

    assert state.duration_mode == "manual"
    assert state.target_duration_seconds == 15.0


def test_back_to_auto_after_a_cut_returns_to_the_fit_not_the_override() -> None:
    """Auto means the card's ask fitted to the pool, as it does without --duration."""
    state = _fitted(AppState(duration_mode="auto", target_duration=1.0), 15)
    switch_to_manual(state)
    set_manual_minutes(state, 4.0)

    set_auto(state)

    assert state.target_duration == 1.0
    assert state.target_duration_seconds == 15.0
