"""'Cut again' runs over the same ticked pool; 'Start Over' forgets the cut (#778)."""

from __future__ import annotations

from immich_memories.ui.pages.step2_review import _start_over_selection, reset_for_recut
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


def _state_after_a_cut() -> AppState:
    state = AppState()
    state.clips = [make_clip("v1"), make_clip("v2")]
    state.selected_clip_ids = {"v1"}
    state.selected_photo_ids = {"p1"}
    state.pipeline_result = {"selected_clips": []}
    state.editorial_attempt_dir = None
    state.previous_cut_asset_ids = frozenset({"v1", "p1"})
    return state


def test_cut_again_keeps_the_ticks_and_the_reference_cut() -> None:
    state = _state_after_a_cut()

    reset_for_recut(state)

    assert state.pipeline_result is None
    assert state.selected_clip_ids == {"v1"}
    assert state.selected_photo_ids == {"p1"}
    assert state.previous_cut_asset_ids == frozenset({"v1", "p1"})


def test_cut_again_over_an_untouched_pool_ticks_everything() -> None:
    state = _state_after_a_cut()
    state.selected_clip_ids = set()
    state.selected_photo_ids = set()

    reset_for_recut(state)

    assert state.selected_clip_ids == {"v1", "v2"}


def test_start_over_forgets_the_cut_so_ticks_are_plain_exclusions_again() -> None:
    state = _state_after_a_cut()

    _start_over_selection(state)

    assert state.previous_cut_asset_ids is None
