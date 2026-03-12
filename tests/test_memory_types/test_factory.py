"""Tests for memory type factory and preset registry."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from immich_memories.memory_types.date_builders import build_month, build_on_this_day, build_season
from immich_memories.memory_types.factory import create_preset, list_memory_types
from immich_memories.memory_types.presets import MemoryPreset
from immich_memories.memory_types.registry import MemoryType


class TestCreatePresetYearInReview:
    """Year in Review preset uses calendar year with default weights."""

    def test_creates_preset(self) -> None:
        preset = create_preset(MemoryType.YEAR_IN_REVIEW, year=2024)
        assert isinstance(preset, MemoryPreset)
        assert preset.memory_type == MemoryType.YEAR_IN_REVIEW

    def test_date_range_is_calendar_year(self) -> None:
        preset = create_preset(MemoryType.YEAR_IN_REVIEW, year=2024)
        assert len(preset.date_ranges) == 1
        assert preset.date_ranges[0].start == datetime(2024, 1, 1, 0, 0, 0)
        assert preset.date_ranges[0].end == datetime(2024, 12, 31, 23, 59, 59)

    def test_default_scoring_weights(self) -> None:
        preset = create_preset(MemoryType.YEAR_IN_REVIEW, year=2024)
        assert preset.scoring.face_weight == 0.4
        assert preset.scoring.motion_weight == 0.25

    def test_title_template(self) -> None:
        preset = create_preset(MemoryType.YEAR_IN_REVIEW, year=2024)
        assert "2024" in preset.name


class TestCreatePresetSeason:
    """Season preset uses season date range with boosted motion weight."""

    def test_summer_date_range(self) -> None:
        preset = create_preset(MemoryType.SEASON, year=2024, season="summer")
        expected = build_season("summer", 2024)
        assert preset.date_ranges[0] == expected

    def test_boosted_motion_weight(self) -> None:
        preset = create_preset(MemoryType.SEASON, year=2024, season="summer")
        assert preset.scoring.motion_weight == 0.35

    def test_winter_uses_hemisphere(self) -> None:
        preset = create_preset(MemoryType.SEASON, year=2024, season="winter", hemisphere="south")
        expected = build_season("winter", 2024, hemisphere="south")
        assert preset.date_ranges[0] == expected

    def test_requires_season(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            create_preset(MemoryType.SEASON, year=2024)


class TestCreatePresetPersonSpotlight:
    """Person spotlight preset boosts face weight."""

    def test_boosted_face_weight(self) -> None:
        preset = create_preset(MemoryType.PERSON_SPOTLIGHT, year=2024, person_names=["Alice"])
        assert preset.scoring.face_weight == 0.6

    def test_single_person_filter(self) -> None:
        preset = create_preset(MemoryType.PERSON_SPOTLIGHT, year=2024, person_names=["Alice"])
        assert preset.person_filter.mode == "single"
        assert preset.person_filter.person_names == ["Alice"]

    def test_requires_person(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            create_preset(MemoryType.PERSON_SPOTLIGHT, year=2024)


class TestCreatePresetMultiPerson:
    """Multi-person preset requires co-occurrence by default."""

    def test_boosted_face_weight(self) -> None:
        preset = create_preset(MemoryType.MULTI_PERSON, year=2024, person_names=["Alice", "Bob"])
        assert preset.scoring.face_weight == 0.5

    def test_co_occurrence_default(self) -> None:
        preset = create_preset(MemoryType.MULTI_PERSON, year=2024, person_names=["Alice", "Bob"])
        assert preset.person_filter.require_co_occurrence is True
        assert preset.person_filter.mode == "all_of"

    def test_requires_multiple_people(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            create_preset(MemoryType.MULTI_PERSON, year=2024)


class TestCreatePresetMonthlyHighlights:
    """Monthly highlights preset uses single month range."""

    def test_month_date_range(self) -> None:
        preset = create_preset(MemoryType.MONTHLY_HIGHLIGHTS, year=2024, month=7)
        expected = build_month(7, 2024)
        assert preset.date_ranges[0] == expected

    def test_shorter_default_duration(self) -> None:
        preset = create_preset(MemoryType.MONTHLY_HIGHLIGHTS, year=2024, month=7)
        assert preset.default_duration_minutes == 1

    def test_requires_month(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            create_preset(MemoryType.MONTHLY_HIGHLIGHTS, year=2024)


class TestCreatePresetOnThisDay:
    """On This Day preset uses multi-range lookback."""

    def test_multiple_date_ranges(self) -> None:
        preset = create_preset(
            MemoryType.ON_THIS_DAY,
            target_date=date(2024, 3, 12),
            years_back=3,
        )
        assert len(preset.date_ranges) == 3

    def test_matches_date_builder(self) -> None:
        preset = create_preset(
            MemoryType.ON_THIS_DAY,
            target_date=date(2024, 3, 12),
            years_back=3,
        )
        expected = build_on_this_day(date(2024, 3, 12), years_back=3)
        assert preset.date_ranges == expected

    def test_short_default_duration(self) -> None:
        preset = create_preset(
            MemoryType.ON_THIS_DAY,
            target_date=date(2024, 3, 12),
        )
        assert preset.default_duration_minutes == 2


class TestListMemoryTypes:
    """list_memory_types returns info about all registered types."""

    def test_returns_all_phase1_types(self) -> None:
        types = list_memory_types()
        names = {t["type"] for t in types}
        assert "year_in_review" in names
        assert "season" in names
        assert "person_spotlight" in names
        assert "multi_person" in names
        assert "monthly_highlights" in names
        assert "on_this_day" in names

    def test_each_entry_has_required_fields(self) -> None:
        types = list_memory_types()
        for t in types:
            assert "type" in t
            assert "name" in t
            assert "description" in t


class TestCreatePresetTrip:
    """Trip preset uses explicit date range and travel scoring weights."""

    def test_creates_preset(self) -> None:
        preset = create_preset(
            MemoryType.TRIP,
            year=2024,
            trip_start=date(2024, 6, 12),
            trip_end=date(2024, 6, 18),
            location_name="Barcelona, Spain",
        )
        assert isinstance(preset, MemoryPreset)
        assert preset.memory_type == MemoryType.TRIP

    def test_date_range_matches_trip(self) -> None:
        preset = create_preset(
            MemoryType.TRIP,
            year=2024,
            trip_start=date(2024, 6, 12),
            trip_end=date(2024, 6, 18),
            location_name="Barcelona, Spain",
        )
        assert len(preset.date_ranges) == 1
        assert preset.date_ranges[0].start.month == 6
        assert preset.date_ranges[0].start.day == 12
        assert preset.date_ranges[0].end.month == 6
        assert preset.date_ranges[0].end.day == 18

    def test_title_uses_location(self) -> None:
        preset = create_preset(
            MemoryType.TRIP,
            year=2024,
            trip_start=date(2024, 6, 12),
            trip_end=date(2024, 6, 18),
            location_name="Barcelona, Spain",
        )
        assert "Barcelona" in preset.name
        assert preset.title_template == "{location}"

    def test_scoring_boosts_motion_and_content(self) -> None:
        preset = create_preset(
            MemoryType.TRIP,
            year=2024,
            trip_start=date(2024, 6, 12),
            trip_end=date(2024, 6, 18),
            location_name="Barcelona, Spain",
        )
        assert preset.scoring.motion_weight == 0.3
        assert preset.scoring.content_weight == 0.3
        assert preset.scoring.face_weight == 0.2

    def test_requires_trip_dates(self) -> None:
        with pytest.raises(ValueError, match="trip_start.*required"):
            create_preset(MemoryType.TRIP, year=2024)

    def test_in_list_memory_types(self) -> None:
        types = list_memory_types()
        names = [t["type"] for t in types]
        assert "trip" in names


class TestUnregisteredType:
    """Unregistered memory types raise ValueError."""

    def test_unregistered_type_raises(self) -> None:
        with pytest.raises(ValueError, match="No preset factory"):
            create_preset(MemoryType.HOLIDAY, year=2024)
