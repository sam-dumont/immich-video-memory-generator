"""Signals the analyzer must capture for period classification (#483).

Per-period analysis means every pool generated from now on either carries
these fields or doesn't — a later regeneration is the only recovery, so the
schema goes in before the periods people care about.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.llm_response_parser import (
    SETTING_VALUES,
    ContentAnalysis,
    ContentAnalyzer,
    build_content_analysis_prompt,
)


def parse_content_response(text: str) -> ContentAnalysis:
    """Parse a model response the way the analyzer does."""
    return ContentAnalyzer()._parse_content_response(text)  # noqa: SLF001


class TestSettingIsAClosedVocabulary:
    """Free text gave 39+ values including both "indoor" and "indoors".
    outdoor_share is the one signal that survived every control in the #475
    era-split study, so it has to be exact rather than reconstructed."""

    def test_the_prompt_lists_the_allowed_values(self):
        prompt = build_content_analysis_prompt()

        for value in SETTING_VALUES:
            assert value in prompt

    def test_a_listed_value_is_kept(self):
        result = parse_content_response('{"setting": "outdoor_nature"}')

        assert result.setting == "outdoor_nature"

    def test_an_unlisted_value_is_dropped_rather_than_stored(self):
        """A model that ignores the enum must not reintroduce free text."""
        result = parse_content_response('{"setting": "restaurant patio"}')

        assert result.setting == ""

    def test_a_near_miss_is_normalised(self):
        assert parse_content_response('{"setting": "Outdoor_Nature "}').setting == (
            "outdoor_nature"
        )

    def test_the_vocabulary_distinguishes_indoor_from_outdoor(self):
        """The indoor/outdoor split is what period classification reads; the
        derived helper belongs with its first consumer, not here."""
        assert {"outdoor_nature", "outdoor_urban", "water"} <= set(SETTING_VALUES)
        assert {"indoor_home", "indoor_public"} <= set(SETTING_VALUES)


class TestActivitiesAreCaptured:
    """Measured on clips Immich CLIP search says ARE cycling: the model
    returns ["cycling"] for 10 of 12. An earlier attempt read 1/12 because
    it sampled a race day by API order and got a lanyard, a cardboard box
    and spectators — bad sampling, not a dead field."""

    def test_the_prompt_asks_for_activities(self):
        assert "activities" in build_content_analysis_prompt()

    def test_activities_are_parsed_and_normalised(self):
        assert parse_content_response('{"activities": ["Cycling", "riding"]}').activities == [
            "cycling",
            "riding",
        ]

    def test_missing_activities_are_empty_not_none(self):
        assert parse_content_response('{"description": "x"}').activities == []


class TestActivitiesReachTheDatabase:
    """The column existed with 0 of 10378 rows populated: the field was
    absent from the prompt AND from the segment INSERT."""

    def test_the_analyzer_puts_the_parsed_activities_on_the_segment(self):
        """#485 pays prompt tokens for the answer; nothing copied it onto the
        segment, so the INSERT read None and every row was NULL again (#518)."""
        from immich_memories.analysis.analyzer_models import ScoredSegment
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
        from immich_memories.config_models import AnalysisConfig, AudioContentConfig

        # WHY: replaces the vision LLM, an external network service.
        content = MagicMock()
        content.analyze_segment.return_value = ContentAnalysis(
            description="a rider on a climb",
            activities=["cycling"],
            confidence=0.9,
        )
        analyzer = UnifiedSegmentAnalyzer(
            scorer=MagicMock(content_min_confidence=0.5),
            content_analyzer=content,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        segment = ScoredSegment(start_time=0.0, end_time=3.0)

        analyzer._score_content(Path("/fake.mov"), 0.0, 3.0, segment=segment)  # noqa: SLF001

        assert segment.llm_activities == ["cycling"]

    def test_a_segment_activities_survive_the_round_trip(self, tmp_path, mock_asset):
        from immich_memories.analysis.analyzer_models import ScoredSegment
        from immich_memories.cache.database import VideoAnalysisCache

        cache = VideoAnalysisCache(tmp_path / "cache.db")
        segment = ScoredSegment(start_time=0.0, end_time=5.0)
        segment.total_score = 0.7
        segment.llm_activities = ["cycling"]

        cache.save_analysis(asset=mock_asset, segments=[segment])
        loaded = cache.get_analysis(mock_asset.id)

        assert loaded is not None
        assert loaded.segments[0].llm_activities == ["cycling"]

    def test_activities_come_back_out_of_the_cache_onto_the_clip(self, tmp_path):
        """The projection is what hands cached semantics to review and selection.
        It copied every other semantic field and skipped this one, so a cached
        rerun saw nothing even once the column was populated (#518)."""
        from immich_memories.analysis.analyzer_models import ScoredSegment
        from immich_memories.analysis.cache_projection import apply_cached_segment
        from immich_memories.cache.database import VideoAnalysisCache
        from tests.conftest import make_asset, make_clip

        cache = VideoAnalysisCache(tmp_path / "cache.db")
        segment = ScoredSegment(start_time=0.0, end_time=5.0)
        segment.llm_description = "a rider on a climb"
        segment.llm_activities = ["cycling"]
        cache.save_analysis(make_asset("act-projected"), segments=[segment])

        restored = cache.get_analysis("act-projected")
        assert restored is not None
        clip = make_clip("act-projected")
        apply_cached_segment(clip, restored.segments[0])

        assert clip.llm_activities == ["cycling"]


@pytest.fixture
def mock_asset():
    """WHY a mock: the cache takes an Asset only for identity and timestamps."""
    from datetime import datetime
    from unittest.mock import MagicMock

    asset = MagicMock()
    asset.id = "asset-act"
    asset.checksum = "abc123"
    asset.file_modified_at = datetime(2024, 1, 15, 12, 0, 0)
    asset.file_created_at = datetime(2024, 1, 15, 10, 0, 0)
    asset.duration_seconds = 30.0
    return asset
