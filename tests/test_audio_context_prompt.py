"""The content-analysis prompt, with and without audio context."""

from __future__ import annotations

from immich_memories.analysis.llm_response_parser import (
    CONTENT_ANALYSIS_PROMPT,
    PROMPT_TRANSCRIPT_MAX_CHARS,
    build_content_analysis_prompt,
)

# Hard-coded rather than compared against the constant: comparing against the
# constant would pass even if both changed together, which is the regression this
# guards.
TODAYS_PROMPT = """Describe what you see in this image.

Return JSON with these fields:
- description: What is happening in this scene?
- emotion: What is the mood? (one word: happy, calm, excited, playful, joyful, peaceful)
- interestingness: How memorable is this moment? (0.0 to 1.0)
- quality: How good is the image quality? (0.0 to 1.0)

Example format: {"description": "...", "emotion": "...", "interestingness": 0.7, "quality": 0.8}

JSON:"""


def test_no_transcript_returns_todays_prompt_byte_for_byte():
    """Anyone not using transcription must see no change at all."""
    assert build_content_analysis_prompt() == TODAYS_PROMPT
    assert CONTENT_ANALYSIS_PROMPT == TODAYS_PROMPT


def test_empty_transcript_is_treated_as_no_transcript():
    assert build_content_analysis_prompt("") == TODAYS_PROMPT
    assert build_content_analysis_prompt("   ") == TODAYS_PROMPT


def test_transcript_is_included_with_a_reliability_warning():
    """The vision model is the only hallucination filter available: it sees both."""
    prompt = build_content_analysis_prompt("Il est mignon. Tu veux lui faire une douce ?")

    assert "Il est mignon. Tu veux lui faire une douce ?" in prompt
    assert "may be inaccurate" in prompt
    assert "ignore it if it does not match the image" in prompt
    assert prompt.endswith("JSON:"), "the JSON instruction must stay last"


def test_long_transcript_is_truncated_on_a_word_boundary():
    """30s of speech is far more text than a slice produced, and small vision models
    have tight context -- the Ollama provider already drops to two images to stay
    under Moondream's 2048-token limit."""
    words = " ".join(["mot"] * 500)

    prompt = build_content_analysis_prompt(words)

    quoted = prompt.split('"')[1]
    assert len(quoted) <= PROMPT_TRANSCRIPT_MAX_CHARS + 1  # +1 for the ellipsis
    assert quoted.endswith("…")
    assert "mot mot" in quoted
    assert not quoted.rstrip("…").endswith("mo"), "must not cut mid-word"
