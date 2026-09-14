"""The wire-text guard keeps out what can break a parse, not what looks foreign."""

from __future__ import annotations

import pytest

from immich_memories.analysis.strict_json import (
    bounded_model_text,
    final_json_object,
    is_safe_model_text,
    model_text_rows,
)

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


def test_non_text_is_not_coerced_into_a_reason() -> None:
    """A missing field arrives as None, and None must not become the string 'None'."""
    assert bounded_model_text(None, max_chars=200) is None
    assert bounded_model_text(12, max_chars=200) is None


def test_a_fenced_answer_still_yields_its_one_object() -> None:
    """Models wrap JSON in Markdown unprompted; the fence is not a second object."""
    assert final_json_object('{"keep":[1]}\n```') == {"keep": [1]}


def test_a_typographic_terminator_is_the_one_repair_worth_making() -> None:
    """Measured: one curly quote where the terminator belonged voided a whole cut."""
    raw = '{\n  "reason": "the peak moment”\n}'

    assert final_json_object(raw) == {"reason": "the peak moment"}


@pytest.mark.parametrize(
    "raw",
    [
        '{"keep":[1]} and here is why I chose it',
        '{"keep":[1]',
        "[1, 2]",
        "no object at all",
        '{"keep":“1”}',
    ],
)
def test_anything_that_is_not_one_complete_object_fails_closed(raw: str) -> None:
    assert final_json_object(raw) is None


def test_trailing_prose_is_accepted_only_when_the_caller_asked_for_it() -> None:
    """Explanations after the object are fine; a second brace means a second answer."""
    assert final_json_object('{"keep":[1]} and here is why', allow_trailing_commentary=True) == {
        "keep": [1]
    }
    assert final_json_object('{"keep":[1]} see {later}', allow_trailing_commentary=True) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (["one", "two"], ["one", "two"]),
        ([], []),
        ("one", ["one"]),
        ("", []),
        ("   ", []),
    ),
)
def test_a_bare_string_is_read_as_the_one_row_it_says(value: object, expected: list) -> None:
    """Measured on a local 35B: a one-sentence answer to a list-of-sentences field."""
    assert model_text_rows(value) == expected


@pytest.mark.parametrize("value", (None, 12, True, {"one": 1}, ("one",)))
def test_widening_the_container_does_not_widen_its_contents(value: object) -> None:
    """Only a list and a string are shapes this field can have; the rest still fail closed."""
    assert model_text_rows(value) is None
