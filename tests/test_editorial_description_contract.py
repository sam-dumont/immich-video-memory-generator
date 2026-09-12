"""The compact caption envelope accepts two literal facts and hedges the rest."""

from __future__ import annotations

import pytest

from immich_memories.analysis.editorial_description_contract import (
    DESCRIPTION_MAX_CHARS,
    SETTING_HEDGE,
    SETTING_MAX_CHARS,
    validate_envelope,
)


def test_a_usable_answer_is_kept_as_one_line_each() -> None:
    """The model wraps its own output; a wrapped line breaks every downstream card."""
    envelope = validate_envelope(
        {"description": "A runner\n  crosses  a street.", "setting": " outdoor road "}
    )

    assert envelope.description == "A runner crosses a street."
    assert envelope.setting == "outdoor road"


@pytest.mark.parametrize(
    "value",
    [
        "not an envelope",
        {"description": "A runner crosses a street."},
        {"description": "A runner crosses a street.", "setting": "road", "extra": "field"},
        {"description": 12, "setting": "road"},
        {"description": "A runner crosses a street.", "setting": None},
        {"description": "   ", "setting": "road"},
        {"description": "x" * (DESCRIPTION_MAX_CHARS + 1), "setting": "road"},
        {"description": "A runner crosses a street.", "setting": "x" * (SETTING_MAX_CHARS + 1)},
    ],
)
def test_an_answer_outside_the_measured_shape_is_refused(value) -> None:
    with pytest.raises(ValueError):
        validate_envelope(value)


@pytest.mark.parametrize(
    "setting",
    [
        "",
        "   ",
        "1234",
        "outdoor road,",
        "outdoor road-",
        "the scene shows a road",
        "This scene is a road",
        "the image shows a road",
        "the photo shows a road",
        "settings vary here",
        "a" * SETTING_MAX_CHARS,
    ],
)
def test_a_setting_that_says_nothing_visible_is_hedged_rather_than_shipped(setting: str) -> None:
    """A truncated or self-describing fragment reads as evidence it is not."""
    envelope = validate_envelope({"description": "A runner crosses a street.", "setting": setting})

    assert envelope.setting == SETTING_HEDGE
