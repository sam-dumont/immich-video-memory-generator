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
