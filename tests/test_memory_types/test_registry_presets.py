"""Tests for memory_types registry and presets."""

from datetime import datetime

from immich_memories.memory_types.presets import (
    MemoryPreset,
    PersonFilter,
    ScoringProfile,
)
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange


class TestMemoryTypeEnum:
    """Tests for MemoryType enum values and behavior."""

    def test_all_phase1_values_exist(self) -> None:
        expected = {
            "year_in_review",
            "season",
            "person_spotlight",
            "multi_person",
            "monthly_highlights",
            "on_this_day",
        }
        phase1 = {
            MemoryType.YEAR_IN_REVIEW,
            MemoryType.SEASON,
            MemoryType.PERSON_SPOTLIGHT,
            MemoryType.MULTI_PERSON,
            MemoryType.MONTHLY_HIGHLIGHTS,
            MemoryType.ON_THIS_DAY,
        }
        assert {str(m) for m in phase1} == expected

    def test_all_phase2_values_exist(self) -> None:
        expected = {"holiday", "trip", "then_and_now"}
        phase2 = {
            MemoryType.HOLIDAY,
            MemoryType.TRIP,
            MemoryType.THEN_AND_NOW,
        }
        assert {str(m) for m in phase2} == expected

    def test_total_enum_count(self) -> None:
        assert len(MemoryType) == 9

    def test_is_str_enum(self) -> None:
        assert isinstance(MemoryType.YEAR_IN_REVIEW, str)
        assert MemoryType.YEAR_IN_REVIEW == "year_in_review"

    def test_string_comparison(self) -> None:
        assert MemoryType.SEASON == "season"
        assert MemoryType.TRIP == "trip"


class TestScoringProfile:
    """Tests for ScoringProfile dataclass."""

    def test_defaults(self) -> None:
        profile = ScoringProfile()
        assert profile.face_weight == 0.4
        assert profile.motion_weight == 0.25
        assert profile.stability_weight == 0.2
        assert profile.audio_weight == 0.15
        assert profile.content_weight == 0.0
        assert profile.duration_weight == 0.15

    def test_to_dict(self) -> None:
        profile = ScoringProfile()
        result = profile.to_dict()
        assert result == {
            "face_weight": 0.4,
            "motion_weight": 0.25,
            "stability_weight": 0.2,
            "audio_weight": 0.15,
            "content_weight": 0.0,
            "duration_weight": 0.15,
        }

    def test_custom_weights(self) -> None:
        profile = ScoringProfile(face_weight=0.8, content_weight=0.5)
        assert profile.face_weight == 0.8
        assert profile.content_weight == 0.5
        # Other defaults remain
        assert profile.motion_weight == 0.25

    def test_custom_weights_in_to_dict(self) -> None:
        profile = ScoringProfile(face_weight=0.9)
        result = profile.to_dict()
        assert result["face_weight"] == 0.9


class TestPersonFilter:
    """Tests for PersonFilter dataclass."""

    def test_defaults(self) -> None:
        pf = PersonFilter()
        assert pf.mode == "any"
        assert pf.person_names == []
        assert pf.require_co_occurrence is False

    def test_custom_values(self) -> None:
        pf = PersonFilter(
            mode="all_of",
            person_names=["Alice", "Bob"],
            require_co_occurrence=True,
        )
        assert pf.mode == "all_of"
        assert pf.person_names == ["Alice", "Bob"]
        assert pf.require_co_occurrence is True

    def test_person_names_are_independent(self) -> None:
        """Each instance gets its own list (no shared mutable default)."""
        pf1 = PersonFilter()
        pf2 = PersonFilter()
        pf1.person_names.append("Alice")
        assert pf2.person_names == []


class TestMemoryPreset:
    """Tests for MemoryPreset dataclass."""

    def test_creation_with_all_fields(self) -> None:
        date_range = DateRange(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 12, 31),
        )
        preset = MemoryPreset(
            memory_type=MemoryType.YEAR_IN_REVIEW,
            name="Year in Review 2025",
            description="A look back at your best moments of 2025",
            date_ranges=[date_range],
            person_filter=PersonFilter(),
            scoring=ScoringProfile(),
            title_template="Your {year} in Review",
            subtitle_template="Best moments of {year}",
            default_duration_minutes=5,
        )
        assert preset.memory_type == MemoryType.YEAR_IN_REVIEW
        assert preset.name == "Year in Review 2025"
        assert len(preset.date_ranges) == 1
        assert preset.subtitle_template == "Best moments of {year}"
        assert preset.default_duration_minutes == 5

    def test_optional_fields_default_to_none(self) -> None:
        date_range = DateRange(
            start=datetime(2025, 6, 1),
            end=datetime(2025, 8, 31),
        )
        preset = MemoryPreset(
            memory_type=MemoryType.SEASON,
            name="Summer 2025",
            description="Summer highlights",
            date_ranges=[date_range],
            person_filter=PersonFilter(),
            scoring=ScoringProfile(),
            title_template="Summer {year}",
        )
        assert preset.subtitle_template is None
        assert preset.default_duration_minutes is None
