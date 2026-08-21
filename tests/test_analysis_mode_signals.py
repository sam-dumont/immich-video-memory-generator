"""Signals the analyzer must capture for period classification (#483).

Per-period analysis means every pool generated from now on either carries
these fields or doesn't — a later regeneration is the only recovery, so the
schema goes in before the periods people care about.
"""

from __future__ import annotations

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
