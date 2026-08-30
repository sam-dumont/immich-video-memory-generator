"""Tests for memory_types registry and presets."""

from datetime import datetime

import pytest

from immich_memories.memory_types.presets import MemoryPreset, PersonFilter, person_filter_for
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

    def test_album_value_exists(self) -> None:
        assert str(MemoryType.ALBUM) == "album"

    def test_total_enum_count(self) -> None:
        assert len(MemoryType) == 11

    def test_is_str_enum(self) -> None:
        assert isinstance(MemoryType.YEAR_IN_REVIEW, str)
        assert MemoryType.YEAR_IN_REVIEW == "year_in_review"

    def test_string_comparison(self) -> None:
        assert MemoryType.SEASON == "season"
        assert MemoryType.TRIP == "trip"


class TestPersonFilter:
    """Tests for PersonFilter dataclass."""

    def test_defaults(self) -> None:
        pf = PersonFilter()
        assert pf.mode == "any"
        assert not pf.person_names
        assert not pf.require_co_occurrence

    def test_custom_values(self) -> None:
        pf = PersonFilter(
            mode="all_of",
            person_names=["Alice", "Bob"],
            require_co_occurrence=True,
        )
        assert pf.mode == "all_of"
        assert pf.person_names == ["Alice", "Bob"]
        assert pf.require_co_occurrence

    def test_person_names_are_independent(self) -> None:
        """Each instance gets its own list (no shared mutable default)."""
        pf1 = PersonFilter()
        pf2 = PersonFilter()
        pf1.person_names.append("Alice")
        assert not pf2.person_names


class TestPersonFilterFor:
    """One rule for what a list of names means, on every memory type."""

    def test_several_names_intersect(self) -> None:
        """Both on the picture — the CLI's semantics, now the preset's too."""
        pf = person_filter_for(["Alice", "Bob"])

        assert pf.person_names == ["Alice", "Bob"]
        assert pf.require_co_occurrence

    def test_several_names_can_be_unioned_explicitly(self) -> None:
        pf = person_filter_for(["Alice", "Bob"], person_match="or")

        assert pf.mode == "any"
        assert not pf.require_co_occurrence

    def test_no_names_narrows_nothing(self) -> None:
        assert not person_filter_for(None).person_names
        assert not person_filter_for([]).person_names


class TestBuriedFields:
    """#666: three fields no code ever read, and the pins that keep them dead.

    They were never functional, so they were removed outright rather than
    deprecated. Building a preset that still names one has to fail loudly —
    a silently ignored keyword is how they survived this long.
    """

    @pytest.mark.parametrize("dead", ["scoring", "title_template", "subtitle_template"])
    def test_naming_a_buried_field_is_an_error(self, dead: str) -> None:
        with pytest.raises(TypeError, match=dead):
            MemoryPreset(
                memory_type=MemoryType.YEAR_IN_REVIEW,
                name="2025 Memories",
                description="A look back",
                date_ranges=[DateRange(start=datetime(2025, 1, 1), end=datetime(2025, 12, 31))],
                person_filter=PersonFilter(),
                **{dead: "whatever it used to hold"},
            )

    def test_the_scoring_profile_is_gone_from_the_package(self) -> None:
        """Its only converter, SceneScorer.from_profile, went with it."""
        import immich_memories.memory_types as memory_types
        from immich_memories.analysis.scoring import SceneScorer

        assert not hasattr(memory_types, "ScoringProfile")
        assert not hasattr(SceneScorer, "from_profile")
