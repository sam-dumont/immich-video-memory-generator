"""The brief offers what `generate --memory-type` accepts, and a cut needs a scope."""

from __future__ import annotations

from immich_memories.cli import main as cli
from immich_memories.timeperiod import calendar_year
from immich_memories.ui.pages.memory_brief import MEMORY_TYPE_LABELS, begin_cut
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
