"""Each question is asked in one order first, and in the other only where the answer decides.

The 30B's reject-only answers are order-unstable: the same block reordered agrees with itself at
about half the weak set. So one order's doubt never moves a shot on its own; it is what makes
the other order worth asking. Standing is not one of those questions: the facts answer it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from immich_memories.analysis.editorial_story_standing import StandingGate
from tests.editorial_thin_fixtures import (
    DOUBTFUL,
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

    assert asked(judge, "thesis-fit-")
    assert all(stage.endswith("-source") for stage in asked(judge, "thesis-fit-"))


def test_the_model_is_never_asked_whether_a_picture_stands(tmp_path):
    film = Film()
    draft(film, 24, captions={5: UNSTEADY, 17: DOUBTFUL})

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    assert asked(judge, "thesis-fit-")
    assert not asked(judge, "standing-")


def test_one_orders_doubt_asks_the_other_order_and_never_removes_a_shot_alone(tmp_path):
    film = Film()
    draft(film, 24, captions={17: DOUBTFUL})

    judge, payload, _cut, _newcomers = polish(tmp_path, film)

    assert any(stage.endswith("-hashed") for stage in asked(judge, "thesis-fit-"))
    assert payload["verdicts"]["d017"]["state"] == "weak"
    assert "d017" not in payload["removed_by_the_vote"]


def standing_gate(*, life: bool, pictures: int) -> StandingGate:
    return StandingGate(
        lambda _asset: 2,
        line_of=lambda _asset: "a line",
        life=lambda _asset: life,
        unit_by_asset={"p": ("fam", {"asset_id": "p", "kind": "still", "favourite": False})},
        pictures_of={"S": pictures},
    )


@pytest.mark.parametrize(
    ("weight", "life", "pictures"),
    [("glimpse", True, 10), ("minor", True, 2), ("minor", False, 10)],
    ids=["glimpse", "story of two", "lifeless minor"],
)
def test_only_a_zero_score_refuses_a_picture_with_no_context_to_serve(weight, life, pictures):
    gate = standing_gate(life=life, pictures=pictures)

    gate.scores["p"] = 1
    assert gate.stands("p", weight, "S")
    gate.scores["p"] = 0
    assert not gate.stands("p", weight, "S")


def test_a_block_every_shot_of_which_the_owner_protects_is_not_voted_on(tmp_path):
    film = Film()
    draft(film, 24, favourites=range(12))

    judge, _payload, _cut, _newcomers = polish(tmp_path, film)

    assert asked(judge, "thesis-fit-") == ["thesis-fit-1-source"]


def test_a_seat_picks_in_one_order(tmp_path):
    film = Film()
    draft(film, 12, captions={5: UNSTEADY})

    judge, _payload, _cut, newcomers = polish(tmp_path, film)

    assert newcomers
    assert asked(judge, "story-pick-")
    assert not [stage for stage in asked(judge, "story-pick-") if "reversed" in stage]
