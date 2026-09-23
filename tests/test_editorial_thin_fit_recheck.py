"""A newcomer is judged again in the company of its own block, not the whole cut."""

from __future__ import annotations

from datetime import timedelta

from tests.editorial_thin_fixtures import JUNK, START, Film, polish


def draft(film: Film, size: int, *, junk=(40,), empty=(), seat_caption="the family together"):
    """`size` shots; the `junk` ones are voted out, and a story in `empty` has nothing else to
    offer its seat."""
    for index in range(size):
        story = f"S{index:02d}"
        when = START + timedelta(days=3 * index)
        pictures = 0 if index in empty else 10
        film.story(
            story, "maybe", pictures, when + timedelta(days=1), caption_of=lambda _n: seat_caption
        )
        caption = JUNK if index in junk else f"people at a table, shot {index}"
        film.draft.append(film.shot(f"d{index:03d}", story, when, caption))


def rechecks(judge) -> list[str]:
    last_pick = max(i for i, stage in enumerate(judge.calls) if stage.startswith("story-pick-"))
    return [stage for stage in judge.calls[last_pick:] if stage.startswith("thesis-fit-")]


def test_one_newcomer_re_asks_only_the_block_it_joined(tmp_path):
    """The shot voted out early in the film leaves no newcomer behind it, so every later block
    of the cut holds different shots than the vote saw; only the newcomer's is asked again."""
    film = Film()
    draft(film, 48, junk=(2, 40), empty=(2,))

    judge, _payload, _cut, newcomers = polish(tmp_path, film)

    assert len(newcomers) == 1
    assert rechecks(judge) == ["thesis-fit-1-source"]


def test_a_newcomer_both_orders_name_is_still_revoked(tmp_path):
    film = Film()
    draft(film, 48, seat_caption=JUNK)

    _judge, payload, cut, newcomers = polish(tmp_path, film)

    assert newcomers == []
    assert len(payload["revoked_by_the_fit_check"]) == 1
    assert payload["revoked_by_the_fit_check"][0] not in {row["asset_id"] for row in cut}


def test_a_swap_takes_its_shots_block_and_an_append_joins_the_block_its_time_falls_in():
    from immich_memories.analysis.editorial_thin_layer import rejoined_blocks
    from immich_memories.analysis.editorial_thin_refill import ThinSlot

    def row(asset, day):
        return {"asset_id": asset, "taken": f"2024-02-{day:02d}T09:00:00"}

    cut = [row("a", 1), row("c", 3), row("d", 4), row("e", 5), row("f", 6)]
    cut += [row("swap", 9), row("late", 28)]
    outcomes = [
        ThinSlot(
            key="D901", story="S1", kind="vote-weak", page=(), replacing="b", filled_by="swap"
        ),
        ThinSlot(key="R001", story="S2", kind="vote-bad", page=(), filled_by="late"),
    ]

    blocks = rejoined_blocks([["a", "b", "c"], ["d", "e", "f"]], cut, {"swap", "late"}, outcomes)

    assert blocks == [["a", "c", "swap"], ["d", "e", "f", "late"]]
