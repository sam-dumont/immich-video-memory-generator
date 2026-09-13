"""The brief offers what `generate --memory-type` accepts, and a cut needs a scope."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from immich_memories.api.models import Person
from immich_memories.cli import main as cli
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import calendar_year
from immich_memories.ui.pages import step1_people, step1_presets
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS, begin_cut
from immich_memories.ui.pages.memory_run import CUT_ALREADY_RUNNING
from immich_memories.ui.pages.step1_presets import CUSTOM_RANGE
from immich_memories.ui.state import AppState
from tests.conftest import make_asset, make_clip


def _cli_memory_types() -> list[str]:
    option = next(param for param in cli.commands["generate"].params if param.name == "memory_type")
    return list(option.type.choices)


def test_the_type_list_is_the_cli_list_plus_the_custom_range() -> None:
    assert list(MEMORY_TYPE_LABELS) == [*_cli_memory_types(), CUSTOM_RANGE]


def test_every_offered_type_carries_a_distinct_label() -> None:
    labels = list(MEMORY_TYPE_LABELS.values())

    assert all(labels)
    assert len(set(labels)) == len(labels)


def test_a_cut_without_a_scope_is_refused_and_arms_nothing() -> None:
    state = AppState(memory_type="year_in_review")

    refusal = begin_cut(state)

    assert refusal is not None
    assert state.pipeline_running is False


def test_an_album_brief_without_an_album_names_what_is_missing() -> None:
    state = AppState(memory_type="album")

    assert "album" in (begin_cut(state) or "").lower()


def test_a_cut_is_refused_while_one_runs_and_keeps_that_run_s_pool() -> None:
    state = AppState(
        memory_type="monthly_highlights",
        date_ranges=[calendar_year(2024)],
        clips=[make_clip("in-flight")],
        pipeline_running=True,
    )

    assert begin_cut(state) == CUT_ALREADY_RUNNING
    assert [clip.asset.id for clip in state.clips] == ["in-flight"]


def test_a_cut_drops_the_previous_pool_and_arms_the_run() -> None:
    state = AppState(
        memory_type="monthly_highlights",
        date_ranges=[calendar_year(2024)],
        clips=[make_clip("old-video")],
        photo_assets=[make_asset("old-photo")],
        pipeline_result={"stats": {}},
    )

    assert begin_cut(state) is None

    assert state.pipeline_running is True
    assert state.clips == []
    assert state.photo_assets == []
    assert state.pipeline_result is None


def _june_brief() -> AppState:
    """A Monthly Highlights brief with two named faces to narrow it with."""
    return AppState(
        memory_type="monthly_highlights",
        date_ranges=[calendar_year(2024)],
        memory_preset_params={"year": 2024, "month": 6},
        people=[Person(id="face-a", name="Adult A"), Person(id="face-b", name="Adult B")],
    )


def _picker_callback(ui: MagicMock, label: str):
    return next(
        call.kwargs["on_change"]
        for call in ui.select.call_args_list
        if call.kwargs.get("label") == label
    )


def test_any_of_these_people_is_a_plain_choice_and_survives_the_preset() -> None:
    """The brief's second person control is the together/any choice, not a condition string."""
    state = _june_brief()
    with (
        # WHY: the wizard reads its state through the NiceGUI session; the real
        # preset application behind the widget is what this test is about.
        patch.object(step1_presets, "get_app_state", return_value=state),
        patch.object(step1_people, "ui", MagicMock()) as ui,
    ):
        step1_people.render_person_picker(
            state, MemoryType.MONTHLY_HIGHLIGHTS, step1_presets._apply_preset_to_state
        )
        _picker_callback(ui, "Only with (optional)")(SimpleNamespace(value=["Adult A", "Adult B"]))
        ui.toggle.call_args.kwargs["on_change"](SimpleNamespace(value="or"))

    assert state.person_match == "or"
    assert state.person_ids == ["face-a", "face-b"]


def test_the_people_condition_is_folded_away_until_one_is_set() -> None:
    """Quoted names, AND, OR and parentheses are not the first screen's business (#887)."""
    state = _june_brief()
    with patch.object(step1_people, "ui", MagicMock()) as ui:
        step1_people.render_person_picker(state, MemoryType.MONTHLY_HIGHLIGHTS, lambda _type: None)
    closed = ui.expansion.call_args

    step1_people._set_grouped_condition(state, '"Adult A" AND "Adult B"')
    with patch.object(step1_people, "ui", MagicMock()) as ui:
        step1_people.render_person_picker(state, MemoryType.MONTHLY_HIGHLIGHTS, lambda _type: None)
    opened = ui.expansion.call_args

    assert closed.args[0] == step1_people.PEOPLE_CONDITION_PANEL
    assert closed.kwargs["value"] is False
    # A filter the page cannot show is a filter nobody can undo.
    assert opened.kwargs["value"] is True
