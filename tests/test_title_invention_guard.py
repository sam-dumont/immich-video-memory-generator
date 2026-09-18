"""A title may reword the facts it was given; it may not name what they never name."""

from __future__ import annotations

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
