"""HOLIDAY and THEN_AND_NOW were enum members with nothing behind them."""

from __future__ import annotations

import pytest

from immich_memories.memory_types.factory import create_preset
from immich_memories.memory_types.registry import MemoryType


def test_a_holiday_preset_spans_years() -> None:
    preset = create_preset(MemoryType.HOLIDAY, holiday="christmas", year=2025, years_back=3)

    assert len(preset.date_ranges) == 3
    assert preset.memory_type is MemoryType.HOLIDAY


def test_a_holiday_preset_is_named_after_the_holiday() -> None:
    preset = create_preset(MemoryType.HOLIDAY, holiday="christmas", year=2025)

    assert "Christmas" in preset.name


def test_an_explicit_date_is_named_by_its_date() -> None:
    """A household holiday has no name to print, so print the date."""
    preset = create_preset(MemoryType.HOLIDAY, holiday="07-04", year=2025)

    assert preset.name


def test_then_and_now_produces_two_periods() -> None:
    """The whole shape of the type: one early window, one recent one."""
    preset = create_preset(MemoryType.THEN_AND_NOW, year=2025, years_back=10)

    assert len(preset.date_ranges) == 2
    now, then = preset.date_ranges  # most recent first, like the other multi-range types
    assert now.start.year == 2025
    assert then.start.year == 2015


def test_then_and_now_refuses_a_gap_of_zero() -> None:
    """Then and now with no gap is just now."""
    with pytest.raises(ValueError, match="years_back"):
        create_preset(MemoryType.THEN_AND_NOW, year=2025, years_back=0)


class TestDateResolution:
    """The CLI must turn the new types into the right ranges, not just accept them."""

    def test_holiday_resolves_to_one_window_per_year(self) -> None:
        from immich_memories.cli._date_resolution import resolve_date_range

        result = resolve_date_range(
            2025,
            None,
            None,
            None,
            None,
            memory_type="holiday",
            holiday="christmas",
            years_back=3,
        )

        assert isinstance(result, list)
        assert [r.start.year for r in result] == [2025, 2024, 2023]
        assert all(r.start.month == 12 for r in result)

    def test_holiday_without_a_holiday_is_a_usage_error(self) -> None:
        import click

        from immich_memories.cli._date_resolution import resolve_date_range

        with pytest.raises(click.UsageError, match="--holiday"):
            resolve_date_range(2025, None, None, None, None, memory_type="holiday")

    def test_then_and_now_resolves_to_two_years_apart(self) -> None:
        from immich_memories.cli._date_resolution import resolve_date_range

        result = resolve_date_range(
            2025,
            None,
            None,
            None,
            None,
            memory_type="then_and_now",
            years_back=10,
        )

        assert [r.start.year for r in result] == [2025, 2015]

    def test_a_moving_holiday_resolves_per_year(self) -> None:
        from immich_memories.cli._date_resolution import resolve_date_range

        result = resolve_date_range(
            2025,
            None,
            None,
            None,
            None,
            memory_type="holiday",
            holiday="easter",
            years_back=2,
        )

        assert [(r.start.month, r.start.day) for r in result] == [(4, 18), (3, 29)]


def test_multi_range_types_are_ordered_most_recent_first() -> None:
    """The CLI derives its display span as `start=ranges[-1].start, end=ranges[0].end`.

    That only works on a descending list. Then-and-now returned [then, now] and
    the run recorded a span of 2025-01-01 to 2017-12-31 — start after end.
    """
    from immich_memories.cli._date_resolution import resolve_date_range
    from immich_memories.timeperiod import DateRange

    for kwargs in (
        {"memory_type": "then_and_now", "years_back": 8},
        {"memory_type": "holiday", "holiday": "christmas", "years_back": 3},
    ):
        ranges = resolve_date_range(2025, None, None, None, None, **kwargs)
        span = DateRange(start=ranges[-1].start, end=ranges[0].end)

        assert span.start < span.end, f"{kwargs['memory_type']} derived an inverted span"
