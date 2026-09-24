"""Titles and captions set in capitals follow each script's own capital rules."""

from __future__ import annotations

import pytest

from immich_memories.titles.letter_case import display_upper


@pytest.mark.parametrize(
    ("text", "capitals"),
    [
        ("Ηράκλειο, Ελλάδα", "ΗΡΑΚΛΕΙΟ, ΕΛΛΑΔΑ"),
        ("Δευτέρα 5", "ΔΕΥΤΕΡΑ 5"),
        ("Ρόδος", "ΡΟΔΟΣ"),
    ],
)
def test_greek_capitals_drop_the_stress_accent(text: str, capitals: str) -> None:
    assert display_upper(text) == capitals


def test_a_greek_accent_that_split_two_vowels_becomes_a_diaeresis() -> None:
    # Without it "ΜΑΙΟΣ" reads as the diphthong αι.
    assert display_upper("Μάιος") == "ΜΑΪΟΣ"
    assert display_upper("Ευρωπαϊκή") == "ΕΥΡΩΠΑΪΚΗ"


def test_other_scripts_keep_their_accents() -> None:
    assert display_upper("Île d'Oléron, Ελλάδα") == "ÎLE D'OLÉRON, ΕΛΛΑΔΑ"
    assert display_upper("Zürich") == "ZÜRICH"


def test_a_trip_title_names_a_greek_place_without_accents() -> None:
    from datetime import date

    from immich_memories.titles._trip_titles import generate_trip_title

    title = generate_trip_title("Ηράκλειο, Ελλάδα", date(2025, 7, 1), date(2025, 7, 7))

    assert "ΗΡΑΚΛΕΙΟ, ΕΛΛΑΔΑ" in title
