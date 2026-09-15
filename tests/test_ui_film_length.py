"""The wizard's length card reports the film, not the sum of the source holds."""

from __future__ import annotations

from immich_memories.config_loader import Config
from immich_memories.processing.timeline_budget import TimelinePlan
from immich_memories.ui.pages.film_length import film_length_stat
from immich_memories.ui.state import AppState
from tests.conftest import make_clip

_PLAN = TimelinePlan(
    target_duration=60.0,
    content_budget=49.5,
    title_budget=10.5,
    title_duration=3.5,
    ending_duration=7.0,
    divider_duration=2.0,
    max_dividers=0,
)


def _state(plan: TimelinePlan | None) -> AppState:
    state = AppState()
    state.config = Config()
    state.timeline_plan = plan
    state.generation_options = {"transition": "Smart (mix of fades & cuts)"}
    return state


def test_length_card_reports_the_film_and_not_the_sum_of_the_holds() -> None:
    clips = [make_clip(f"clip-{index}", duration=4.4) for index in range(18)]

    label, value = film_length_stat(_state(_PLAN), clips)

    assert label == "Film length"
    assert value == "≈0:53"


def test_length_card_names_the_quantity_it_has_before_a_timeline_exists() -> None:
    clips = [make_clip(f"clip-{index}", duration=4.4) for index in range(18)]

    label, value = film_length_stat(_state(None), clips)

    assert label == "Pictures & video"
    assert value == "1:19"


def test_the_finished_file_replaces_the_estimate_with_its_measured_length() -> None:
    """Once the tracker has ffprobed the artifact, Step 4 states the fact, not the estimate."""
    from immich_memories.ui.pages.film_length import measured_film_label

    state = _state(_PLAN)
    assert measured_film_label(state) is None

    state.output_duration_seconds = 52.73

    assert measured_film_label(state) == "Length: 0:52"
