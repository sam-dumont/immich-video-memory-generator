"""A film's length comes from the material its period holds, not from a preset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.api.models import Asset, AssetType
from immich_memories.planning.auto_duration import (
    DURATION_FROM_DURATION_FLAG,
    DURATION_FROM_MATERIAL,
    DURATION_FROM_PRESET,
    DURATION_FROM_SHORT_FORM,
    candidate_day_count,
    decide_memory_duration,
)
from immich_memories.timeperiod import DateRange

_MONTH = DateRange(
    start=datetime(2024, 2, 1, tzinfo=UTC),
    end=datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
)
_MONTH_PRESET_SECONDS = 60.0


def _photo(asset_id: str, when: datetime) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )


def _photographed_month(days: int, per_day: int = 6) -> list[Asset]:
    """A February photographed on ``days`` of its days."""
    first = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
    return [
        _photo(f"p-{day}-{index}", first + timedelta(days=day))
        for day in range(days)
        for index in range(per_day)
    ]


def _decide(
    photos: list[Asset],
    *,
    requested_seconds: float | None = None,
    requested_source: str = DURATION_FROM_MATERIAL,
):
    return decide_memory_duration(
        [],
        photos,
        requested_seconds=requested_seconds,
        requested_source=requested_source,
        preset_seconds=_MONTH_PRESET_SECONDS,
        memory_type="monthly_highlights",
        candidate_days=candidate_day_count([_MONTH]),
        avg_clip_duration=5.0,
        photo_duration=4.0,
        title_duration=3.5,
        ending_duration=4.0,
    )


def test_a_sparsely_photographed_month_gets_a_shorter_film_than_a_dense_one() -> None:
    sparse = _decide(_photographed_month(4))
    dense = _decide(_photographed_month(22))

    assert sparse.seconds < _MONTH_PRESET_SECONDS < dense.seconds
    assert sparse.source == dense.source == DURATION_FROM_MATERIAL


def test_an_explicit_duration_wins_over_the_material() -> None:
    decision = _decide(
        _photographed_month(22),
        requested_seconds=180.0,
        requested_source=DURATION_FROM_DURATION_FLAG,
    )

    assert decision.seconds == 180.0
    assert decision.source == DURATION_FROM_DURATION_FLAG


def test_short_form_pins_its_preset_against_a_dense_month() -> None:
    decision = _decide(
        _photographed_month(22),
        requested_seconds=30.0,
        requested_source=DURATION_FROM_SHORT_FORM,
    )

    assert decision.seconds == 30.0
    assert decision.source == DURATION_FROM_SHORT_FORM


def test_a_month_with_no_material_keeps_its_preset_floor() -> None:
    decision = _decide([])

    assert decision.seconds == _MONTH_PRESET_SECONDS
    assert decision.source == DURATION_FROM_PRESET


def test_the_record_names_what_decided_and_the_evidence_behind_it() -> None:
    record = _decide(_photographed_month(12)).as_record()

    assert record["source"] == DURATION_FROM_MATERIAL
    assert record["photographed_days"] == 12
    assert record["seconds"] <= record["capacity_seconds"]


def test_a_film_never_exceeds_what_a_thin_month_can_fill() -> None:
    """Two photographs on one day cannot carry a minute of film."""
    one_day = datetime(2024, 2, 14, 12, 0, tzinfo=UTC)

    decision = _decide([_photo("a", one_day), _photo("b", one_day)])

    assert decision.seconds <= decision.capacity_seconds
    assert decision.seconds < _MONTH_PRESET_SECONDS


def test_overlapping_windows_are_counted_once() -> None:
    rolling_year = DateRange(
        start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 12, 31, tzinfo=UTC)
    )
    inside_it = DateRange(
        start=datetime(2024, 6, 1, tzinfo=UTC), end=datetime(2024, 6, 30, tzinfo=UTC)
    )

    assert candidate_day_count([rolling_year, inside_it]) == 366


def test_separate_windows_add_up() -> None:
    """A holiday through the years draws from one short window per year."""
    windows = [
        DateRange(
            start=datetime(year, 12, 24, tzinfo=UTC),
            end=datetime(year, 12, 26, tzinfo=UTC),
        )
        for year in (2022, 2023, 2024)
    ]

    assert candidate_day_count(windows) == 9


def test_an_occasion_keeps_the_length_its_active_hours_bought() -> None:
    """A special day's preset already came from its material; its days are not coverage."""
    noon = datetime(2024, 2, 7, 12, 0, tzinfo=UTC)
    all_day = [_photo(f"o-{index}", noon) for index in range(40)]

    decision = decide_memory_duration(
        [],
        all_day,
        requested_seconds=None,
        requested_source=DURATION_FROM_MATERIAL,
        preset_seconds=90.0,
        memory_type="special_day",
        candidate_days=1,
        avg_clip_duration=5.0,
        photo_duration=4.0,
        title_duration=3.5,
        ending_duration=4.0,
    )

    assert decision.editorial_seconds == 90.0
