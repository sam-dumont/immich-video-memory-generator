"""What a day is called once the grounding guard has taken the model's title away.

The guard works: an ungrounded title is dropped. What replaced it was the
day's own description with the end cut off — "Six images captured between
07:32 and 16:06, tracing a route from weathered apar" — which is a caption
wearing a title's hat. A live scan over 2010 put several of those in the
catalogue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.special_day import ask_if_special

# The entry #714 was reported for, at the width the catalogue stored it.
_A_DESCRIPTION = "Six images captured between 07:32 and 16:06, tracing a route from weathered apar"


def _day(city: str | None = None) -> list[SimpleNamespace]:
    """A day with nothing to place it, which is when the guard blanks a title."""
    return [
        SimpleNamespace(
            file_created_at=datetime(2010, 8, 14, hour, tzinfo=UTC),
            exif_info=SimpleNamespace(
                city=city,
                state=None,
                country=city and "Belgium",
                latitude=None,
                longitude=None,
            ),
            people=[],
        )
        for hour in range(8, 16)
    ]


def _answers(*payloads: str):
    """The model, answering each question in turn."""
    # WHY: the LLM server is the external boundary; these are its answers.
    return patch("immich_memories.analysis.special_day._ask", side_effect=list(payloads))


def test_a_dropped_title_is_asked_for_once_more() -> None:
    """The model usually can write a grounded title on a second try."""
    with _answers(
        '{"special": true, "title": "The Ambassador\'s glass", "what": "a walk"}',
        '{"title": "A long afternoon out"}',
    ):
        verdict = ask_if_special(_day(), llm_config=SimpleNamespace())

    assert verdict.title == "A long afternoon out"


def test_the_day_is_asked_again_once_and_only_when_its_title_was_taken() -> None:
    """A scan makes a handful of live calls a year; a retry is a real cost.

    Once, never twice: a model told what the evidence shows that answers with
    an invention anyway will not be talked round on a third try.
    """
    kept = '{"special": true, "title": "A long afternoon out", "what": "a walk"}'
    invented = '{"special": true, "title": "The Ambassador\'s glass", "what": "a walk"}'

    with _answers(kept) as asked:
        ask_if_special(_day(), llm_config=SimpleNamespace())
    assert asked.call_count == 1, "a title the day supports is not questioned"

    with _answers(invented, invented) as asked:
        ask_if_special(_day(), llm_config=SimpleNamespace())
    assert asked.call_count == 2, "and a title it does not is asked for exactly once more"

    ordinary = '{"special": false, "title": "The Ambassador\'s glass", "what": "a walk"}'
    with _answers(ordinary) as asked:
        ask_if_special(_day(), llm_config=SimpleNamespace())
    assert asked.call_count == 1, "an ordinary day is discarded, so its title is not worth a call"


def test_a_day_that_knows_where_it_was_is_named_after_the_place() -> None:
    """Two inventions in a row, and the day still recorded one true thing."""
    with _answers(
        f'{{"special": true, "title": "A drive day at Circuit de Faraway", "what": "{_A_DESCRIPTION}"}}',
        '{"title": "Racing at Faraway"}',
    ):
        verdict = ask_if_special(_day(city="Gantvile"), llm_config=SimpleNamespace())

    assert verdict.title == "A day in Gantvile"


def test_a_day_with_no_place_is_named_after_what_it_was() -> None:
    """The 2010 days had no location at all — that is why the guard fired.

    All that is left of such a day is what the model said it was, and that
    field is a title whenever it reads as one.
    """
    with _answers(
        '{"special": true, "title": "The Ambassador\'s glass", "what": "Children\'s camp activities"}',
        '{"title": "An evening at Faraway"}',
    ):
        verdict = ask_if_special(_day(), llm_config=SimpleNamespace())

    assert verdict.title == "Children's camp activities"


def test_the_days_description_never_wears_a_titles_hat() -> None:
    """The reported bug, from the answer that produced it.

    `what` comes back as a caption as readily as a name. A sentence with the
    clock in it and the end cut off is not a title, and no amount of having
    nothing better to show makes it one.
    """
    with _answers(
        f'{{"special": true, "title": "The Ambassador\'s glass", "what": "{_A_DESCRIPTION}"}}',
        '{"title": "An evening at Faraway"}',
    ):
        verdict = ask_if_special(_day(), llm_config=SimpleNamespace())

    assert _A_DESCRIPTION not in verdict.title
    assert verdict.title == "", "and there is nothing honest left to call this day"


def test_a_day_nothing_can_name_is_not_written_down() -> None:
    """An entry with an empty title is read as its description everywhere.

    `days-due` and the wizard both print the title or fall back to `what`, so
    a catalogue entry the scan could not title is exactly how the description
    reached a card. A day the library cannot name is refused instead.
    """
    from immich_memories.automation.special_day_scan import scan_year

    # Enough of the day to clear the structural bar and reach the model at all.
    day = _day() * 3

    # WHY: ask_if_special is the LLM call; here it comes back unable to name the day.
    with patch(
        "immich_memories.automation.special_day_scan.ask_if_special",
        return_value=SimpleNamespace(
            special=True, title="", subtitle="", what=_A_DESCRIPTION, window=None
        ),
    ):
        found = scan_year(day, llm_config=None, home=None)

    assert found == [], "a day with no title it can keep is not a find"
