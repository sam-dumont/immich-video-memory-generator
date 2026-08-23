"""The wizard has to hold every window a memory spans, not just the first.

Three memory types build more than one date range — On This Day and Holiday
build one window per year, Then and Now builds two years far apart. The wizard
stored a single `date_range`, so it kept `date_ranges[0]` and silently dropped
the rest. Since the builders order their windows most-recent-first, that made
"This day through the years" a memory about this year.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange
from immich_memories.ui.pages.step1_presets import _apply_preset_to_state
from immich_memories.ui.state import AppState


def _apply(memory_type: MemoryType, **params) -> AppState:
    state = AppState(memory_preset_params=dict(params))
    # WHY: the wizard reads its state through a per-session accessor; this is
    # the seam that hands the function a state object instead of a live session.
    with patch("immich_memories.ui.pages.step1_presets.get_app_state", return_value=state):
        _apply_preset_to_state(memory_type)
    return state


def test_on_this_day_keeps_every_year_the_card_promises() -> None:
    """The card says "This day through the years"; five years must survive."""
    state = _apply(MemoryType.ON_THIS_DAY)

    assert len(state.date_ranges) == 5


def _year(y: int) -> DateRange:
    return DateRange(start=datetime(y, 1, 1), end=datetime(y, 12, 31, 23, 59, 59))


def _state_with_ranges(*years: int) -> AppState:
    return AppState(
        immich_url="http://immich.test",
        immich_api_key="k",
        date_ranges=[_year(y) for y in years],
    )


def test_the_wizard_fetches_each_window_not_the_span_between_them() -> None:
    """Three windows means three queries.

    Querying the span instead would pull every asset between the oldest and
    newest window — for a Then and Now, a decade of library it has no interest
    in, at Immich's expense and then the analyzer's.
    """
    from immich_memories.ui.pages.step2_loading import _fetch_assets

    state = _state_with_ranges(2020, 2023, 2026)
    client = MagicMock()
    client.get_videos_for_date_range.return_value = []

    # WHY: Immich is the external boundary — this stands in for the library read.
    with patch("immich_memories.ui.pages.step2_loading.SyncImmichClient") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        _fetch_assets(state)

    queried = [c.args[0] for c in client.get_videos_for_date_range.call_args_list]
    assert queried == [_year(2020), _year(2023), _year(2026)]


def test_an_asset_in_two_overlapping_windows_is_fetched_once() -> None:
    """Holiday windows two days either side can collide on consecutive years."""
    from immich_memories.ui.pages.step2_loading import _fetch_assets

    state = _state_with_ranges(2025, 2026)
    shared = MagicMock()
    shared.id = "in-both-windows"
    client = MagicMock()
    client.get_videos_for_date_range.return_value = [shared]

    # WHY: Immich is the external boundary — this stands in for the library read.
    with patch("immich_memories.ui.pages.step2_loading.SyncImmichClient") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        assets = _fetch_assets(state)

    assert client.get_videos_for_date_range.call_count == 2
    assert [a.id for a in assets] == ["in-both-windows"]
