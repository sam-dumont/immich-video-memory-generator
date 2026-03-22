"""Tests for calendar-driven candidate detectors."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from immich_memories.automation.calendar_detectors import (
    MonthlyDetector,
    PersonSpotlightDetector,
    YearlyDetector,
)
from immich_memories.automation.candidates import MemoryCandidate, make_memory_key


def _make_config():
    return MagicMock()


def _make_person(name: str, *, thumbnail: str | None = "/thumb.jpg") -> MagicMock:
    p = MagicMock()
    p.name = name
    p.thumbnail_path = thumbnail
    p.id = f"id-{name.lower()}"
    return p


# ---------------------------------------------------------------------------
# MonthlyDetector
# ---------------------------------------------------------------------------
class TestMonthlyDetector:
    def test_produces_candidates_for_ungenerated_months(self):
        today = date(2026, 3, 15)
        assets = {"2026-02": 50, "2026-01": 30, "2025-12": 20}
        detector = MonthlyDetector()

        result = detector.detect(assets, [], set(), _make_config(), today)

        assert len(result) == 3
        assert all(isinstance(c, MemoryCandidate) for c in result)
        assert result[0].memory_type == "monthly_highlights"
        assert result[0].date_range_start == date(2026, 2, 1)
        assert result[0].date_range_end == date(2026, 2, 28)
        assert result[0].asset_count == 50

    def test_skips_already_generated_months(self):
        today = date(2026, 3, 15)
        assets = {"2026-02": 50, "2026-01": 30}
        feb_key = make_memory_key("monthly_highlights", date(2026, 2, 1), date(2026, 2, 28))

        result = MonthlyDetector().detect(assets, [], {feb_key}, _make_config(), today)

        assert len(result) == 1
        assert result[0].date_range_start == date(2026, 1, 1)

    def test_handles_empty_assets(self):
        result = MonthlyDetector().detect({}, [], set(), _make_config(), date(2026, 3, 1))
        assert result == []

    def test_skips_months_with_zero_assets(self):
        today = date(2026, 3, 15)
        # Only Feb has assets, Jan is in range but missing from dict
        assets = {"2026-02": 10}

        result = MonthlyDetector().detect(assets, [], set(), _make_config(), today)

        assert len(result) == 1
        assert result[0].date_range_start == date(2026, 2, 1)

    def test_recent_months_score_higher(self):
        today = date(2026, 6, 15)
        assets = {
            "2026-05": 10,
            "2026-04": 10,
            "2026-03": 10,
            "2026-02": 10,
            "2026-01": 10,
            "2025-12": 10,
        }

        result = MonthlyDetector().detect(assets, [], set(), _make_config(), today)

        assert len(result) == 6
        scores = [c.score for c in result]
        # Scores should be monotonically decreasing
        assert scores == sorted(scores, reverse=True)
        assert scores[0] > scores[-1]

    def test_most_recent_month_gets_special_reason(self):
        today = date(2026, 3, 15)
        assets = {"2026-02": 50, "2026-01": 30}

        result = MonthlyDetector().detect(assets, [], set(), _make_config(), today)

        assert "most recent month" in result[0].reason
        assert "never generated" in result[1].reason

    def test_handles_year_boundary(self):
        """January should look back into prior year's months."""
        today = date(2026, 1, 10)
        assets = {"2025-12": 40, "2025-11": 25}

        result = MonthlyDetector().detect(assets, [], set(), _make_config(), today)

        assert result[0].date_range_start == date(2025, 12, 1)
        assert result[0].date_range_end == date(2025, 12, 31)

    def test_memory_key_format(self):
        today = date(2026, 4, 1)
        assets = {"2026-03": 10}

        result = MonthlyDetector().detect(assets, [], set(), _make_config(), today)

        expected_key = "monthly_highlights:2026-03-01:2026-03-31:"
        assert result[0].memory_key == expected_key


# ---------------------------------------------------------------------------
# YearlyDetector
# ---------------------------------------------------------------------------
class TestYearlyDetector:
    def test_produces_candidate_for_past_year(self):
        today = date(2026, 2, 1)
        assets = {"2025-01": 10, "2025-06": 20, "2025-12": 30}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        assert len(result) == 1
        assert result[0].memory_type == "year_in_review"
        assert result[0].date_range_start == date(2025, 1, 1)
        assert result[0].date_range_end == date(2025, 12, 31)
        assert result[0].asset_count == 60

    def test_skips_before_jan_15(self):
        """Don't propose year-in-review too early — wait for late imports."""
        today = date(2026, 1, 10)
        assets = {"2025-06": 100}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        assert result == []

    def test_allows_on_jan_15(self):
        today = date(2026, 1, 15)
        assets = {"2025-06": 100}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        assert len(result) == 1

    def test_skips_already_generated_years(self):
        today = date(2026, 2, 1)
        assets = {"2025-06": 100}
        key = make_memory_key("year_in_review", date(2025, 1, 1), date(2025, 12, 31))

        result = YearlyDetector().detect(assets, [], {key}, _make_config(), today)

        assert result == []

    def test_handles_empty_assets(self):
        result = YearlyDetector().detect({}, [], set(), _make_config(), date(2026, 6, 1))
        assert result == []

    def test_multiple_years_sorted_recent_first(self):
        today = date(2026, 6, 1)
        assets = {"2024-03": 50, "2025-07": 80, "2023-01": 10}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        years = [c.date_range_start.year for c in result]
        assert years == sorted(years, reverse=True)

    def test_recent_years_score_higher(self):
        today = date(2026, 6, 1)
        assets = {"2024-03": 50, "2025-07": 80}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        assert result[0].score > result[1].score
        assert result[0].date_range_start.year == 2025

    def test_does_not_propose_current_year(self):
        """Current year can't be a year-in-review (it's not over)."""
        today = date(2026, 6, 1)
        assets = {"2026-03": 50}

        result = YearlyDetector().detect(assets, [], set(), _make_config(), today)

        # 2026 requires cutoff of Jan 15 2027, so today < cutoff
        assert result == []


# ---------------------------------------------------------------------------
# PersonSpotlightDetector
# ---------------------------------------------------------------------------
class TestPersonSpotlightDetector:
    def test_produces_candidates_for_top_people(self):
        people = [_make_person("Alice"), _make_person("Bob"), _make_person("Carol")]
        today = date(2026, 3, 1)

        result = PersonSpotlightDetector().detect({}, people, set(), _make_config(), today)

        assert len(result) == 3
        assert result[0].memory_type == "person_spotlight"
        assert result[0].person_names == ["Alice"]
        assert result[0].date_range_start == date(2025, 1, 1)
        assert result[0].date_range_end == date(2025, 12, 31)

    def test_skips_already_generated(self):
        people = [_make_person("Alice"), _make_person("Bob")]
        today = date(2026, 3, 1)
        alice_key = make_memory_key(
            "person_spotlight", date(2025, 1, 1), date(2025, 12, 31), ["alice"]
        )

        result = PersonSpotlightDetector().detect({}, people, {alice_key}, _make_config(), today)

        assert len(result) == 1
        assert result[0].person_names == ["Bob"]

    def test_handles_no_people(self):
        result = PersonSpotlightDetector().detect({}, [], set(), _make_config(), date(2026, 3, 1))
        assert result == []

    def test_skips_unnamed_people(self):
        people = [_make_person(""), _make_person("Alice")]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        assert len(result) == 1
        assert result[0].person_names == ["Alice"]

    def test_skips_people_without_thumbnail(self):
        people = [_make_person("Alice", thumbnail=None), _make_person("Bob")]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        assert len(result) == 1
        assert result[0].person_names == ["Bob"]

    def test_limits_to_top_5(self):
        people = [_make_person(f"Person{i}") for i in range(10)]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        assert len(result) == 5

    def test_first_person_scores_highest(self):
        people = [_make_person("Alice"), _make_person("Bob"), _make_person("Carol")]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] > scores[-1]

    def test_reason_includes_ordinal(self):
        people = [_make_person("Alice"), _make_person("Bob")]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        assert "1st most featured" in result[0].reason
        assert "2nd most featured" in result[1].reason

    def test_memory_key_uses_lowercase_name(self):
        people = [_make_person("Alice")]

        result = PersonSpotlightDetector().detect(
            {}, people, set(), _make_config(), date(2026, 3, 1)
        )

        assert "alice" in result[0].memory_key
