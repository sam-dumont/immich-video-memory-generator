"""A dense run of flagged captures holds the clean ones between them; one or two do not."""

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_exposure_chains import held_chains


def _rows(*offsets_and_flags):
    """(id, taken, flagged) rows at the given minute offsets from one fixed instant."""
    start = datetime(2024, 2, 3, 9, 0, tzinfo=UTC)
    return [
        (f"a{index}", start + timedelta(minutes=minutes), flagged)
        for index, (minutes, flagged) in enumerate(offsets_and_flags)
    ]


def test_two_flagged_captures_hold_only_themselves():
    """Half the run, but two flagged is a corner of a scene rather than the scene."""
    held = held_chains(_rows((0, False), (2, True), (4, True), (6, False)))

    assert held == {}


def test_three_flagged_of_six_hold_the_whole_run():
    held = held_chains(_rows((0, False), (2, True), (4, True), (6, True), (8, False), (10, False)))

    assert set(held) == {f"a{n}" for n in range(6)}
    assert held["a0"].size == 6 and held["a0"].flagged == 3 and held["a0"].swept_in
    assert not held["a1"].swept_in


def test_three_flagged_of_five_hold_the_whole_run():
    held = held_chains(_rows((0, True), (2, True), (4, True), (6, False), (8, False)))

    assert set(held) == {f"a{n}" for n in range(5)}
    assert held["a4"].size == 5 and held["a4"].flagged == 3 and held["a4"].swept_in


def test_three_flagged_of_eight_is_under_half_and_holds_nothing():
    """The count clears its floor, the share does not: most of this run is ordinary."""
    held = held_chains(
        _rows(
            (0, True),
            (2, True),
            (4, True),
            (6, False),
            (8, False),
            (10, False),
            (12, False),
            (14, False),
        )
    )

    assert held == {}


def test_a_lone_hit_holds_only_itself():
    """One breastfeeding picture must not hold the minutes of family pictures around it."""
    held = held_chains(_rows((0, False), (2, True), (4, False)))

    assert held == {}


def test_a_gap_over_the_window_starts_a_new_run():
    """Six minutes is not five: the third flagged capture no longer shares their run."""
    held = held_chains(_rows((0, True), (2, True), (8, True), (10, False)))

    assert held == {}


def test_a_run_broken_by_the_window_keeps_its_own_dense_half():
    held = held_chains(_rows((0, True), (2, True), (4, True), (6, False), (13, False), (15, False)))

    assert set(held) == {"a0", "a1", "a2", "a3"}
    assert held["a3"].size == 4 and held["a3"].flagged == 3 and held["a3"].swept_in


def test_the_rows_need_no_order():
    dense = _rows((0, True), (2, True), (4, True), (6, False))

    assert held_chains(dense) == held_chains(list(reversed(dense)))


def _captures(count, *, clip_flagged):
    """Live Photos two minutes apart, each still clean and each clip as given."""
    from types import SimpleNamespace

    from tests.test_editorial_preparation import asset

    start = datetime(2024, 2, 3, 9, 0, tzinfo=UTC)
    stills, lines, heads = {}, {}, {}
    for index in range(count):
        taken = start + timedelta(minutes=2 * index)
        still = asset(f"s{index}").model_copy(
            update={"file_created_at": taken, "live_photo_video_id": f"c{index}"}
        )
        stills[still.id] = still
        lines[still.id] = SimpleNamespace(heads=(("nsfw_marqo", "no"),))
        heads[f"c{index}"] = {"nsfw_marqo": "yes" if index < clip_flagged else "no"}
    return stills, lines, heads


def test_a_live_photo_is_one_capture_and_its_flagged_clip_flags_it():
    """A clip is the same capture as its still: it neither dilutes the run nor goes unseen."""
    from immich_memories.analysis.editorial_exposure_chains import chain_holds_for

    stills, lines, heads = _captures(4, clip_flagged=3)

    held = chain_holds_for(stills, lines, heads)

    assert set(held) == set(stills)
    assert held["s0"].size == 4 and held["s0"].flagged == 3
    assert held["s3"].swept_in and not held["s0"].swept_in


def test_clean_clips_leave_a_run_of_clean_stills_alone():
    from immich_memories.analysis.editorial_exposure_chains import chain_holds_for

    stills, lines, heads = _captures(4, clip_flagged=0)

    assert chain_holds_for(stills, lines, heads) == {}
