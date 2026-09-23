"""A seat is filled by picking first and asking the gates about the pick, never about its page."""

from __future__ import annotations

import re
from datetime import timedelta

from tests.editorial_thin_fixtures import START, UNSTEADY, Film, polish


def small_draft(film: Film, *, refused_caption: str = UNSTEADY) -> None:
    """Twelve shots; one the standing gate refuses, in a story of a thousand pictures whose
    first two pictures the gate refuses as well."""
    for index in range(12):
        story = f"S{index:02d}"
        when = START + timedelta(days=3 * index)
        film.story(
            story,
            "maybe",
            1000 if index == 5 else 10,
            when + timedelta(days=1),
            caption_of=lambda n: UNSTEADY if n < 2 else "the family together",
        )
        caption = refused_caption if index == 5 else f"people at a table, shot {index}"
        film.draft.append(film.shot(f"d{index:03d}", story, when, caption))


def rows_asked(judge, prefix: str) -> list[int]:
    return [
        len(re.findall(r"^(?:P|M)\d+:? ", prompt, re.MULTILINE))
        for stage, prompt in judge.prompts
        if stage.startswith(prefix)
    ]


def test_standing_is_asked_only_of_the_rows_the_picker_chose(tmp_path):
    film = Film()
    small_draft(film)

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    asked = rows_asked(judge, "standing-")
    # the draft's twelve in both orders, then the chosen rows only, never the story's thousand
    assert sum(asked) <= 2 * 12 + 2 * 2


def test_a_choice_the_standing_gate_refuses_is_picked_again_from_the_same_page(tmp_path):
    film = Film()
    small_draft(film)

    _judge, payload, cut, newcomers = polish(tmp_path, film)

    assert len(newcomers) == 1
    assert UNSTEADY not in film.lines[newcomers[0]]
    assert [slot["outcome"] for slot in payload["slots"]] == ["seated"]
    assert "d005" not in {row["asset_id"] for row in cut}


def test_the_picker_is_shown_at_most_twelve_rows(tmp_path):
    film = Film()
    small_draft(film)

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    assert rows_asked(judge, "story-pick-")
    assert max(rows_asked(judge, "story-pick-")) <= 12
