"""Signals the analyzer must capture for period classification (#483).

Per-period analysis means every pool generated from now on either carries
these fields or doesn't — a later regeneration is the only recovery, so the
schema goes in before the periods people care about.
"""

from __future__ import annotations

from datetime import datetime
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
    """llm_activities exists as a column with 0 of 10378 rows populated: the
    field is not in the prompt and nothing writes it. It is the only path to
    detecting an activity period — a cycling month is identical to a family
    month on every other measured axis."""

    def test_the_prompt_asks_for_activities(self):
        assert "activities" in build_content_analysis_prompt()

    def test_activities_are_parsed(self):
        result = parse_content_response('{"activities": ["Cycling", "riding"]}')

        assert result.activities == ["cycling", "riding"]

    def test_missing_activities_are_empty_not_none(self):
        assert parse_content_response('{"description": "x"}').activities == []


@pytest.fixture
def mock_asset():
    """WHY a mock: the cache takes an Asset only for identity and timestamps."""
    asset = MagicMock()
    asset.id = "asset-act"
    asset.checksum = "abc123"
    asset.file_modified_at = datetime(2024, 1, 15, 12, 0, 0)
    asset.file_created_at = datetime(2024, 1, 15, 10, 0, 0)
    asset.duration_seconds = 30.0
    return asset


class TestActivitiesReachTheDatabase:
    """The column exists with 0 of 10378 rows populated — the INSERT omits it,
    so even a populated field would be dropped on the way to disk."""

    def test_a_segment_activities_survive_the_round_trip(self, tmp_path, mock_asset):
        from immich_memories.analysis.analyzer_models import ScoredSegment
        from immich_memories.cache.database import VideoAnalysisCache

        cache = VideoAnalysisCache(tmp_path / "cache.db")
        segment = ScoredSegment(start_time=0.0, end_time=5.0)
        segment.total_score = 0.7
        segment.llm_activities = ["cycling", "riding"]

        cache.save_analysis(asset=mock_asset, segments=[segment])
        loaded = cache.get_analysis(mock_asset.id)

        assert loaded is not None
        assert loaded.segments[0].llm_activities == ["cycling", "riding"]


class TestFacePositionsSurviveScoring:
    """score_scene computes face positions and _score_visual threw them away —
    processing/transforms.py takes them for framing and was fed nothing."""

    def test_the_visual_scorer_keeps_the_geometry(self):
        import inspect

        from immich_memories.analysis import unified_analyzer

        source = inspect.getsource(unified_analyzer.UnifiedSegmentAnalyzer._score_visual)

        assert "face_positions" in source
