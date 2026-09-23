"""Each question is asked in one order first, and in the other only where the answer decides.

The 30B's reject-only answers are order-unstable: the same block reordered agrees with itself at
about half the weak set. So one order's doubt never moves a shot on its own; it is what makes
the other order worth asking.
"""

from __future__ import annotations

import re
from datetime import timedelta

from tests.editorial_thin_fixtures import (
    DOUBTFUL,
    ONE_ORDER,
    START,
    UNSTEADY,
    Film,
    polish,
)


def draft(film: Film, size: int, *, captions=None, tier="maybe", favourites=()) -> None:
    captions = captions or {}
    for index in range(size):
        story = f"S{index:02d}"
        when = START + timedelta(days=3 * index)
        film.story(story, tier, 10, when + timedelta(days=1))
        caption = captions.get(index, f"people at a table, shot {index}")
        film.draft.append(
            film.shot(f"d{index:03d}", story, when, caption, favourite=index in favourites)
        )


def asked(judge, prefix: str) -> list[str]:
    return [stage for stage in judge.calls if stage.startswith(prefix)]


def test_a_draft_nothing_is_wrong_with_is_asked_each_question_in_one_order(tmp_path):
    film = Film()
    draft(film, 24)

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    assert asked(judge, "standing-") and asked(judge, "thesis-fit-")
    assert all(stage.endswith("-source") for stage in asked(judge, "standing-"))
    assert all(stage.endswith("-source") for stage in asked(judge, "thesis-fit-"))


def test_one_orders_doubt_asks_the_other_order_and_never_removes_a_shot_alone(tmp_path):
    film = Film()
    draft(film, 24, captions={5: ONE_ORDER, 17: DOUBTFUL})

    judge, payload, cut, _newcomers = polish(tmp_path, film)

    assert any(stage.endswith("-hashed") for stage in asked(judge, "standing-"))
    assert any(stage.endswith("-hashed") for stage in asked(judge, "thesis-fit-"))
    # a minor story's lively shot needs one order's approval, and the hashed order gave it
    assert "d005" in {row["asset_id"] for row in cut}
    assert payload["verdicts"]["d017"]["state"] == "weak"
    assert "d017" not in payload["removed_by_the_vote"]


def test_a_glimpse_one_order_doubts_is_asked_again_before_the_gate_decides(tmp_path):
    film = Film()
    draft(film, 12, captions={5: ONE_ORDER}, tier="background")

    judge, payload, _cut, _newcomers = polish(tmp_path, film)

    assert any(stage.endswith("-hashed") for stage in asked(judge, "standing-"))
    # a glimpse has no context to serve, so it still has to stand by both orders
    assert [row["asset_id"] for row in payload["refused_by_the_gates"]] == ["d005"]


def test_a_block_every_shot_of_which_the_owner_protects_is_not_voted_on(tmp_path):
    film = Film()
    draft(film, 24, favourites=range(12))

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    assert asked(judge, "thesis-fit-") == ["thesis-fit-1-source"]


def test_standing_never_asks_a_block_of_one(tmp_path):
    film = Film()
    draft(film, 13)

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    rows = [
        len(re.findall(r"^P\d+: ", prompt, re.MULTILINE))
        for stage, prompt in judge.prompts
        if stage.startswith("standing-")
    ]
    assert rows and min(rows) > 2


def test_a_seat_picks_in_one_order(tmp_path):
    film = Film()
    draft(film, 12, captions={5: UNSTEADY})

    judge, _payload, _cut, newcomers = polish(tmp_path, film)

    assert newcomers
    assert asked(judge, "story-pick-")
    assert not [stage for stage in asked(judge, "story-pick-") if "reversed" in stage]
