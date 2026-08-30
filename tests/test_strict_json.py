"""The wire-text guard keeps out what can break a parse, not what looks foreign."""

from __future__ import annotations

import pytest

from immich_memories.analysis.strict_json import bounded_model_text, is_safe_model_text

# Verbatim from the local model during the 2026-08-26 Selects probes. Nothing
# asked it for a curly apostrophe or an em dash; it produces them unprompted,
# and under the old 32..126 rule each one silently voided a whole episode
# reading. A Belgian library adds cafe, Noel, Liege on top.
REAL_MODEL_REASONS = (
    "Frame 8 captures the baby’s most alert and engaged expression — eyes wide",
    "Frame 3 is the peak because it captures the moment where the man’s posture settles",
    "Une après-midi à Liège, au café",
)


@pytest.mark.parametrize("reason", REAL_MODEL_REASONS)
def test_real_model_prose_is_usable_text(reason: str) -> None:
    """Discarding a decision over its punctuation loses the decision, not the risk."""
    assert is_safe_model_text(reason, max_chars=200)
    assert bounded_model_text(reason, max_chars=200) == reason


@pytest.mark.parametrize(
    "value",
    (
        'a reason with a " in it',
        "a reason with a \\ in it",
        "two\nlines",
        "a\ttab",
        "a\x00null",
        "trailing space ",
        "",
    ),
)
def test_text_that_can_break_a_parse_or_a_line_is_still_refused(value: str) -> None:
    """Quotes, escapes, control characters and untrimmed text stay out."""
    assert not is_safe_model_text(value, max_chars=200)


def test_length_is_still_the_one_property_worth_coercing() -> None:
    """A reason nine characters long still says what it meant."""
    assert bounded_model_text("é" * 40, max_chars=10) == "é" * 10
