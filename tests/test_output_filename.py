"""Tests for output filename generation based on memory type context."""

from __future__ import annotations

from datetime import date

from immich_memories.ui.filename_builder import build_output_filename, get_divider_mode


class TestBuildOutputFilename:
    """Test filename generation for different memory type presets."""

    def test_custom_with_person_and_month_range(self):
        """Custom date range with a person selected: use person name + month + year."""
        result = build_output_filename(
            memory_type="custom",
            preset_params={},
            person_name="sam",
            date_start=date(2026, 3, 1),
            date_end=date(2026, 3, 31),
        )
        assert result == "sam_march_2026_memories.mp4"

    def test_custom_no_person(self):
        """Custom date range, no person: everyone + readable date."""
        result = build_output_filename(
            memory_type="custom",
            preset_params={},
            person_name=None,
            date_start=date(2026, 3, 1),
            date_end=date(2026, 3, 31),
        )
        assert result == "everyone_march_2026_memories.mp4"

    def test_multi_person_preset(self):
        """Multi-person preset: join person names."""
        result = build_output_filename(
            memory_type="multi_person",
            preset_params={
                "person_names": ["sam", "emile"],
                "year": 2026,
            },
            person_name=None,
            date_start=date(2026, 3, 1),
            date_end=date(2026, 3, 31),
        )
        assert result == "sam_emile_march_2026_memories.mp4"

    def test_multi_person_with_many_names_truncates(self):
        """More than 3 person names: truncate and add 'and_others'."""
        result = build_output_filename(
            memory_type="multi_person",
            preset_params={
                "person_names": ["alice", "bob", "carol", "dave"],
                "year": 2025,
            },
            person_name=None,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "alice_bob_carol_and_others_2025_memories.mp4"

    def test_year_in_review(self):
        """Year in Review preset: person + year."""
        result = build_output_filename(
            memory_type="year_in_review",
            preset_params={"year": 2025},
            person_name="sam",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "sam_2025_memories.mp4"

    def test_year_in_review_no_person(self):
        """Year in Review, no person."""
        result = build_output_filename(
            memory_type="year_in_review",
            preset_params={"year": 2025},
            person_name=None,
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "everyone_2025_memories.mp4"

    def test_season_preset(self):
        """Season preset: person + season + year."""
        result = build_output_filename(
            memory_type="season",
            preset_params={"year": 2025, "season": "summer"},
            person_name="emile",
            date_start=date(2025, 6, 1),
            date_end=date(2025, 8, 31),
        )
        assert result == "emile_summer_2025_memories.mp4"

    def test_person_spotlight(self):
        """Person Spotlight: use person name from preset params."""
        result = build_output_filename(
            memory_type="person_spotlight",
            preset_params={"person_names": ["emile"], "year": 2025},
            person_name="emile",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "emile_2025_memories.mp4"

    def test_monthly_highlights(self):
        """Monthly Highlights: person + month + year."""
        result = build_output_filename(
            memory_type="monthly_highlights",
            preset_params={"year": 2026, "month": 3},
            person_name="sam",
            date_start=date(2026, 3, 1),
            date_end=date(2026, 3, 31),
        )
        assert result == "sam_march_2026_memories.mp4"

    def test_on_this_day(self):
        """On This Day: person + readable date."""
        result = build_output_filename(
            memory_type="on_this_day",
            preset_params={},
            person_name="sam",
            date_start=date(2026, 3, 12),
            date_end=date(2026, 3, 12),
        )
        assert result == "sam_march_12_memories.mp4"

    def test_none_memory_type_fallback(self):
        """No memory type (legacy): fallback to old behavior."""
        result = build_output_filename(
            memory_type=None,
            preset_params={},
            person_name="sam",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "sam_2025_memories.mp4"

    def test_custom_multi_month_range(self):
        """Custom range spanning multiple months: use date range slug."""
        result = build_output_filename(
            memory_type="custom",
            preset_params={},
            person_name="sam",
            date_start=date(2026, 1, 15),
            date_end=date(2026, 4, 20),
        )
        assert result == "sam_jan-apr_2026_memories.mp4"

    def test_sanitizes_special_characters(self):
        """Person names with special chars get sanitized."""
        result = build_output_filename(
            memory_type="year_in_review",
            preset_params={"year": 2025},
            person_name="Jean-Pierre",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        assert result == "jean-pierre_2025_memories.mp4"

    def test_trip_with_location(self):
        """Trip preset with location name: use 'trip' who + sanitized location."""
        result = build_output_filename(
            memory_type="trip",
            preset_params={"location_name": "Lisbon", "year": 2025},
            person_name="sam",
            date_start=date(2025, 7, 10),
            date_end=date(2025, 7, 20),
        )
        assert result == "trip_lisbon_memories.mp4"

    def test_trip_without_location(self):
        """Trip preset without location: fallback to date range."""
        result = build_output_filename(
            memory_type="trip",
            preset_params={"year": 2025},
            person_name="sam",
            date_start=date(2025, 7, 10),
            date_end=date(2025, 7, 20),
        )
        assert result == "trip_july_2025_memories.mp4"

    def test_trip_location_with_special_chars(self):
        """Trip location with commas and special chars gets sanitized."""
        result = build_output_filename(
            memory_type="trip",
            preset_params={"location_name": "Ostend, Belgium", "year": 2025},
            person_name=None,
            date_start=date(2025, 8, 1),
            date_end=date(2025, 8, 10),
        )
        assert result == "trip_ostend_belgium_memories.mp4"


class TestGetDividerMode:
    """Test divider mode selection based on memory type and date range."""

    def test_monthly_highlights_returns_none(self):
        result = get_divider_mode("monthly_highlights", date(2026, 3, 1), date(2026, 3, 31))
        assert result == "none"

    def test_on_this_day_returns_year(self):
        """On This Day spans multiple years: use year dividers."""
        result = get_divider_mode("on_this_day", date(2021, 3, 12), date(2026, 3, 12))
        assert result == "year"

    def test_short_range_returns_none(self):
        """Ranges of 3 months or less: no dividers."""
        result = get_divider_mode("custom", date(2026, 1, 1), date(2026, 3, 31))
        assert result == "none"

    def test_long_same_year_range_returns_month(self):
        """4+ month range within same year: month dividers."""
        result = get_divider_mode("year_in_review", date(2025, 1, 1), date(2025, 12, 31))
        assert result == "month"

    def test_multi_year_range_returns_year(self):
        """Range spanning multiple years: year dividers."""
        result = get_divider_mode("custom", date(2023, 1, 1), date(2026, 3, 31))
        assert result == "year"

    def test_trip_returns_none(self):
        """Trip: no dividers regardless of length."""
        result = get_divider_mode("trip", date(2026, 1, 1), date(2026, 6, 30))
        assert result == "none"

    def test_season_4_months_returns_month(self):
        """Season spanning 4 months: month dividers."""
        result = get_divider_mode("season", date(2025, 6, 1), date(2025, 9, 30))
        assert result == "month"

    def test_no_dates_defaults_to_month(self):
        result = get_divider_mode("year_in_review", None, None)
        assert result == "month"
