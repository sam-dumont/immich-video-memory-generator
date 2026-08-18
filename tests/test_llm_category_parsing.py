"""The subject category has to survive a malformed response, not just clean JSON."""

from __future__ import annotations

from immich_memories.analysis.content_analyzer import get_content_analyzer  # noqa: F401
from immich_memories.analysis.llm_response_parser import ContentAnalyzer


def _partial(text: str):
    return ContentAnalyzer._extract_partial_data(ContentAnalyzer.__new__(ContentAnalyzer), text)


def test_category_survives_the_regex_fallback() -> None:
    """The fallback used to extract description, emotion, interestingness, quality
    and setting, and drop the rest — so a response that failed strict JSON parsing
    lost the one field the subject policy reads."""
    text = (
        'Here you go!\n{"description": "A smartwatch showing running data", '
        '"category": "screen", "subjects": ["watch", "wrist"], "emotion": "calm", '
        '"interestingness": 0.4, "quality": 0.8'
    )

    result = _partial(text)

    assert result.category == "screen"
    assert result.subjects == ["watch", "wrist"]
    assert result.description.startswith("A smartwatch")


def test_a_missing_category_stays_empty() -> None:
    result = _partial('{"description": "Kids on a beach", "emotion": "happy"')
    assert result.category == ""
