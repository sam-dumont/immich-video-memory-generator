"""The wizard can reach the two memory types #443 built for the CLI.

HOLIDAY and THEN_AND_NOW shipped as CLI-only. Both build more than one date
range, which is why they needed the multi-range wizard state before they could
be offered at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.memory_types.registry import MemoryType
from immich_memories.ui.pages.step1_presets import _PRESET_CARDS, _apply_preset_to_state
from immich_memories.ui.state import AppState


def _apply(memory_type: MemoryType, **params) -> AppState:
    state = AppState(memory_preset_params=dict(params))
    # WHY: the wizard reads its state through a per-session accessor.
    with patch("immich_memories.ui.pages.step1_presets.get_app_state", return_value=state):
        _apply_preset_to_state(memory_type)
    return state


def test_both_memory_types_are_offered_as_cards() -> None:
    keys = [card[0] for card in _PRESET_CARDS]

    assert MemoryType.HOLIDAY in keys
    assert MemoryType.THEN_AND_NOW in keys


def _render(key: str, **params) -> AppState:
    """Drive the wizard's param dispatch with the widget layer stubbed out."""
    from immich_memories.ui.pages import step1_presets

    state = AppState(memory_preset_params=dict(params))
    # WHY: the wizard reads its state through a per-session accessor.
    with (
        patch.object(step1_presets, "get_app_state", return_value=state),
        # WHY: NiceGUI widgets need a live client slot; the dispatch under test
        # does not. These two stand in for the whole widget layer.
        patch.object(step1_presets, "ui", MagicMock()),
        patch.object(step1_presets, "im_card", MagicMock()),
    ):
        step1_presets._render_params(key)
    return state


def test_choosing_holiday_gives_the_wizard_a_scope() -> None:
    """A card with no params branch renders nothing and leaves state empty.

    Without windows the wizard's `scope_is_selected` stays false and Step 1
    never completes, so the card would look present and do nothing.
    """
    state = _render(MemoryType.HOLIDAY, holiday="christmas", year=2026, years_back=5)

    assert len(state.date_ranges) == 5
    assert state.scope_is_selected


def test_choosing_then_and_now_gives_the_wizard_both_years() -> None:
    state = _render(MemoryType.THEN_AND_NOW, year=2026, years_back=10)

    assert [r.start.year for r in state.date_ranges] == [2026, 2016]
    assert state.scope_is_selected


def test_then_and_now_titles_name_both_years() -> None:
    """The span reaches the template as its two ends, which is the whole point.

    Without a branch this falls through to the generic long-span fallback and
    becomes "Memories 2016" — the older year alone, which is the one thing a
    then-and-now must not be called.
    """
    from immich_memories.ui.pages.pipeline_title import generate_template_title

    title, subtitle = generate_template_title(
        memory_type="then_and_now", start_date="2016-01-01", end_date="2026-12-31"
    )

    assert "2016" in title
    assert "2026" in title
    assert subtitle == "Then and Now"


def test_holiday_titles_name_the_holiday_not_the_span() -> None:
    """Five Christmases span five years; "Memories 2021" describes none of them."""
    from immich_memories.ui.pages.pipeline_title import generate_template_title

    title, subtitle = generate_template_title(
        memory_type="holiday",
        start_date="2021-12-23",
        end_date="2026-12-27",
        preset_params={"holiday": "christmas"},
    )

    assert title == "Christmas"
    assert subtitle == "Through the Years"
