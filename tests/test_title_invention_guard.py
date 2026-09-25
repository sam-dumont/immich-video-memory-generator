"""A title may reword the facts it was given; it may not name what they never name."""

from __future__ import annotations

import re

import pytest

from immich_memories.titles.llm_titles import TitleSuggestion, invented_name, parse_title_response

FACTS = (
    "Memory type: special_day\n"
    "Span: 2022-08-13 to 2022-08-13 (1 day)\n"
    "The occasion, as catalogued: an outdoor music festival with multiple performances\n"
    "Album the pictures sit in: Riverside Festival 2022\n"
    "Places by day:\n  08-13: Zwevegem\n"
)


def test_a_title_built_from_the_album_name_is_kept():
    assert invented_name("Le jour du festival Riverside", FACTS) is None


def test_a_title_naming_something_no_fact_names_is_caught():
    assert invented_name("Le festival Broken Babies", FACTS) == "Broken"


def test_a_place_written_in_the_films_language_is_not_an_invention():
    facts = "Places by day:\n  07-04: Chóra Sfakíon, Greece\n"
    assert invented_name("Onze jours en Grèce", facts) is None


def test_the_first_word_is_capitalised_by_orthography_and_proves_nothing():
    assert invented_name("Zwevegem sous la pluie", FACTS) is None
    assert invented_name("Dimanche au festival", FACTS) is None


def test_a_reply_is_still_parsed_when_it_only_rewords_its_facts():
    reply = '{"title": "Riverside, samedi", "subtitle": null, "reason": "the album names it"}'
    suggestion = parse_title_response(reply)
    assert isinstance(suggestion, TitleSuggestion)
    assert invented_name(suggestion.title, FACTS) is None


def test_a_month_the_facts_carry_as_a_date_is_not_an_invention():
    facts = "Memory type: monthly_highlights\nSpan: 2024-01-01 to 2024-01-31 (31 days)\n"
    assert invented_name("Porto in January", facts + "Places by day:\n  01-04: Porto\n") is None


def test_a_weekday_or_month_in_another_film_language_is_not_an_invention():
    facts = "Span: 2024-03-03 to 2024-03-03 (1 day)\nPlaces by day:\n  03-03: Berlin\n"
    assert invented_name("Berlin am Sonntag im März", facts) is None
    assert invented_name("Ein Sonntag im März", facts) is None


def test_a_name_beside_a_month_is_still_caught():
    facts = "Span: 2024-01-01 to 2024-01-31 (31 days)\nPlaces by day:\n  01-04: Porto\n"
    assert invented_name("Porto in January with Marcel", facts) == "Marcel"


def test_a_country_opening_the_title_that_no_fact_names_is_caught():
    facts = "Memory type: monthly_highlights\nSpan: 2024-02-01 to 2024-02-29 (29 days)\n"
    assert invented_name("Chypre en février", facts + "Places by day:\n  02-10: Brussels\n") == (
        "Chypre"
    )


def test_a_country_the_facts_name_in_english_may_open_the_title_in_french():
    facts = "Span: 2024-02-01 to 2024-02-29 (29 days)\nPlaces by day:\n  02-10: Nicosia, Cyprus\n"
    assert invented_name("Chypre en février", facts) is None


def test_a_country_is_not_taken_for_a_fact_label_it_happens_to_resemble():
    # "Chypre" is as close to "type" (from "Memory type:") as the spelling tolerance allows.
    facts = "Memory type: monthly_highlights\nPlaces by day:\n  02-10: Brussels\n"
    assert invented_name("Une semaine à Chypre", facts) == "Chypre"


def _country_island_and_region_words() -> set[str]:
    from babel import Locale

    from immich_memories.i18n import SUPPORTED_LOCALES
    from immich_memories.place_names import area_name_groups

    names = {n for group in area_name_groups().values() for n in group}
    for code in SUPPORTED_LOCALES:
        names.update(Locale.parse(code.replace("-", "_")).territories.values())
    return {name.casefold() for name in names if " " not in name}


@pytest.mark.parametrize(
    ("memory_type", "person_names"),
    [("monthly_highlights", None), ("trip", None), ("person_spotlight", ["Emma"])],
)
def test_a_title_prompt_names_no_place_but_the_facts_own(memory_type, person_names):
    # A month film was titled "Chypre en février" off the prompt's own example line.
    from immich_memories.titles.llm_titles import build_title_prompt

    prompt = build_title_prompt(
        memory_type, "fr", "2024-02-01", "2024-02-29", 28,
        daily_locations=["02-10: Brussels"], person_names=person_names,
    )  # fmt: skip
    words = {w.casefold() for w in re.findall(r"[^\W\d_]+", prompt.text)}

    assert not words & _country_island_and_region_words()
