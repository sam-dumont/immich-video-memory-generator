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


def test_an_off_vocabulary_category_is_dropped() -> None:
    """The fallback is taken exactly when the model is ignoring the schema, so it
    is the likeliest source of the free text the closed vocabulary keeps out (#539)."""
    result = _partial('{"description": "x", "category": "a child holding a watch"')
    assert result.category == ""


def test_a_listed_category_survives_odd_casing() -> None:
    """Dropping unlisted values only works if listed ones still get through: the
    model returns "Screen" and "people " often enough to matter."""
    assert _partial('{"description": "x", "category": "Screen "').category == "screen"


def test_free_text_category_is_dropped_on_the_json_path_too() -> None:
    """Well-formed JSON carrying an unlisted category was the wider door: it was
    truncated to 500 chars and stored, where the subject policy read it as a label."""
    build = ContentAnalyzer._build_content_analysis
    assert build({"category": "a wide shot of a lawnmower"}, "").category == ""
    assert build({"category": "l" * 600}, "").category == ""
    assert build({"category": "landscape"}, "").category == "landscape"
