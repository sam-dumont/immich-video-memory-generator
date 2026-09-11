"""What the wizard offers of the holiday memory #443 built for the CLI.

HOLIDAY shipped as CLI-only. It builds more than one date range, which is why
it needed the multi-range wizard state before it could be offered at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.memory_types.registry import MemoryType
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS
from immich_memories.ui.state import AppState


def test_holiday_is_offered() -> None:
    assert MemoryType.HOLIDAY in list(MEMORY_TYPE_LABELS)


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
        step1_presets.render_type_params(key)
    return state


def test_choosing_holiday_gives_the_wizard_a_scope() -> None:
    """A card with no params branch renders nothing and leaves state empty.

    Without windows the wizard's `scope_is_selected` stays false and Step 1
    never completes, so the card would look present and do nothing.
    """
    state = _render(MemoryType.HOLIDAY, holiday="christmas", year=2026, years_back=5)

    assert len(state.date_ranges) == 5
    assert state.scope_is_selected


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
