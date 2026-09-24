"""Dutch, German, Spanish, Italian, Portuguese, Polish and Swedish say "in <place>" right (#1101).

The phrases are the whole trip-title middle: preposition, article or case, the
place in the language, and the country after a comma. A place a language has
no verified rule for gets None, and the title goes without a preposition.
"""

from __future__ import annotations

import pytest

from immich_memories.place_phrases import Place, place_phrase

CASES = [
    # Countries
    ("de", "Italy", "country", "in Italien"),
    ("de", "Switzerland", "country", "in der Schweiz"),
    ("de", "United States", "country", "in den USA"),
    ("de", "Netherlands", "country", "in den Niederlanden"),
    ("de", "Philippines", "country", "auf den Philippinen"),
    ("nl", "Italy", "country", "in Italië"),
    ("nl", "United States", "country", "in de Verenigde Staten"),
    ("nl", "United Kingdom", "country", "in het Verenigd Koninkrijk"),
    ("nl", "Philippines", "country", "op de Filipijnen"),
    ("es", "Italy", "country", "en Italia"),
    ("es", "United Kingdom", "country", "en el Reino Unido"),
    ("es", "Netherlands", "country", "en los Países Bajos"),
    ("it", "France", "country", "in Francia"),
    ("it", "United States", "country", "negli Stati Uniti"),
    ("it", "Maldives", "country", "alle Maldive"),
    ("it", "Japan", "country", "in Giappone"),
    ("pt-BR", "Italy", "country", "na Itália"),
    ("pt-BR", "Japan", "country", "no Japão"),
    ("pt-BR", "United States", "country", "nos Estados Unidos"),
    ("pt-BR", "Canada", "country", "no Canadá"),
    ("pt-PT", "France", "country", "em França"),
    ("pt-PT", "Germany", "country", "na Alemanha"),
    ("pl", "Italy", "country", "we Włoszech"),
    ("pl", "Hungary", "country", "na Węgrzech"),
    ("pl", "United States", "country", "w Stanach Zjednoczonych"),
    ("sv", "Italy", "country", "i Italien"),
    ("sv", "Philippines", "country", "på Filippinerna"),
    # Islands (the owner's examples: Crete, Cyprus)
    ("de", "Crete, Greece", "island", "auf Kreta, Griechenland"),
    ("nl", "Crete, Greece", "island", "op Kreta, Griekenland"),
    ("es", "Crete, Greece", "island", "en Creta, Grecia"),
    ("it", "Crete, Greece", "island", "a Creta, Grecia"),
    ("it", "Sicily, Italy", "island", "in Sicilia, Italia"),
    ("pt-BR", "Madeira, Portugal", "island", "na Madeira, Portugal"),
    ("pl", "Crete, Greece", "island", "na Krecie, Grecja"),
    ("sv", "Crete, Greece", "island", "på Kreta, Grekland"),
    ("de", "Cyprus", "country", "auf Zypern"),
    ("pl", "Cyprus", "country", "na Cyprze"),
    # Regions (Puglia, the Malerweg in Saxony)
    ("de", "Saxony, Germany", "region", "in Sachsen, Deutschland"),
    ("de", "Apulia, Italy", "region", "in Apulien, Italien"),
    ("de", "Tuscany, Italy", "region", "in der Toskana, Italien"),
    ("es", "Apulia, Italy", "region", "en Apulia, Italia"),
    ("it", "Apulia, Italy", "region", "in Puglia, Italia"),
    ("pt-BR", "Saxony, Germany", "region", "na Saxônia, Alemanha"),
    ("pt-PT", "Saxony, Germany", "region", "na Saxónia, Alemanha"),
    ("pl", "Apulia, Italy", "region", "w Apulii, Włochy"),
    ("sv", "Saxony, Germany", "region", "i Sachsen, Tyskland"),
    # Two regions (a road trip)
    (
        "de",
        "Utah and Nevada, United States",
        "regions",
        "in Utah und in Nevada, Vereinigte Staaten",
    ),
    ("pl", "Utah and Nevada, United States", "regions", "w Utah i w Newadzie, Stany Zjednoczone"),
    # Cities
    ("de", "Las Vegas, United States", "city", "in Las Vegas, Vereinigte Staaten"),
    ("it", "Las Vegas, United States", "city", "a Las Vegas, Stati Uniti"),
    ("pt-PT", "Porto, Portugal", "city", "no Porto, Portugal"),
    ("pl", "Las Vegas, United States", "city", "w Las Vegas, Stany Zjednoczone"),
]


@pytest.mark.parametrize(("locale", "english", "kind", "expected"), CASES)
def test_phrase(locale: str, english: str, kind: str, expected: str) -> None:
    assert place_phrase(locale, Place(english, kind)) == expected


@pytest.mark.parametrize(
    ("locale", "english", "kind"),
    [
        ("pl", "Szczebrzeszyn, Poland", "city"),
        ("pl", "Tuvalu", "country"),
        ("de", "Ionian Islands, Greece", "region"),
        ("pt-BR", "Nevada, United States", "region"),
        ("de", "Belgium → France", "countries"),
    ],
)
def test_a_place_without_a_verified_rule_gets_no_phrase(
    locale: str, english: str, kind: str
) -> None:
    assert place_phrase(locale, Place(english, kind)) is None


@pytest.mark.parametrize("locale", ["ru", "ja", "zh-Hans", "ko"])
def test_the_fallback_languages_never_get_a_preposition(locale: str) -> None:
    assert place_phrase(locale, Place("Crete, Greece", "island")) is None
