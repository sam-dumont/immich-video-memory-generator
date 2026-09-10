"""How long the memory runs: the type's own answer, or the owner's override."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from immich_memories.memory_types.registry import MemoryType

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

# What Auto resolves to for the types that do not simply take a preset default.
_AUTO_NOTES: dict[str, str] = {
    MemoryType.TRIP.value: "30 s plus 10 s per active day, from the media once it is loaded",
    MemoryType.ALBUM.value: "4 s per item in the album, from the media once it is loaded",
    MemoryType.SPECIAL_DAY.value: "30 s plus 6 s per active hour of the day",
    "custom": "about 10 minutes per year of range",
}
_DEFAULT_NOTE = "the type's default length"


def duration_label(state: AppState) -> str:
    """One line: the mode and the exact runtime it resolves to."""
    minutes, seconds = divmod(round(state.target_duration_seconds), 60)
    mode = "Auto" if state.duration_mode == "auto" else "Manual"
    return f"{mode} · {minutes}m {seconds:02d}s"


def auto_duration_note(memory_type: str | None) -> str:
    """Where an Auto duration comes from for this memory type."""
    return _AUTO_NOTES.get(memory_type or "", _DEFAULT_NOTE)


def set_manual_minutes(state: AppState, minutes: float) -> None:
    """An exact override; fractional minutes keep their seconds."""
    state.target_duration = minutes
    state.duration_mode = "manual"


def set_auto(state: AppState) -> None:
    """Back to the type's own answer, keeping its last resolved value until the next one."""
    state.duration_mode = "auto"


def render_duration_line(state: AppState) -> None:
    """A switch, then either the resolved runtime or the override field."""
    with ui.row().classes("w-full items-center gap-4 flex-wrap"):
        switch = ui.switch("Auto duration", value=state.duration_mode == "auto").tooltip(
            "The type decides the length; turn off to set an exact target"
        )
        detail = ui.row().classes("items-center gap-3")

        def on_target(e) -> None:
            if e.value is not None:
                set_manual_minutes(state, e.value)

        def paint() -> None:
            detail.clear()
            with detail:
                if state.duration_mode == "auto":
                    ui.label().classes("text-base font-semibold").bind_text_from(
                        state, "target_duration", backward=lambda _value: duration_label(state)
                    )
                    ui.label(auto_duration_note(state.memory_type)).classes("text-xs").style(
                        "color: var(--im-text-secondary)"
                    )
                else:
                    ui.number(
                        "Target duration (min)",
                        value=state.target_duration,
                        min=0.25,
                        max=60,
                        step=0.25,
                        on_change=on_target,
                    ).classes("w-44")

        def on_switch(e) -> None:
            if e.value:
                set_auto(state)
            else:
                set_manual_minutes(state, state.target_duration)
            paint()

        switch.on_value_change(on_switch)
        paint()
