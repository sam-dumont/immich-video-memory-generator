"""A dense run of flagged captures holds the clean ones between them; a lone hit does not."""

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_exposure_chains import held_chains


def _rows(*offsets_and_flags):
    """(id, taken, flagged) rows at the given minute offsets from one fixed instant."""
    start = datetime(2024, 2, 3, 9, 0, tzinfo=UTC)
    return [
        (f"a{index}", start + timedelta(minutes=minutes), flagged)
        for index, (minutes, flagged) in enumerate(offsets_and_flags)
    ]


def test_half_a_chain_flagged_holds_the_whole_chain():
    held = held_chains(_rows((0, False), (2, True), (4, True), (6, False)))

    assert set(held) == {"a0", "a1", "a2", "a3"}
    assert held["a0"].size == 4 and held["a0"].flagged == 2 and held["a0"].swept_in
    assert not held["a1"].swept_in


def test_under_half_holds_only_the_flagged_captures():
    held = held_chains(_rows((0, False), (2, True), (4, True), (6, False), (8, False)))

    assert held == {}


def test_a_lone_hit_holds_only_itself():
    """One breastfeeding picture must not hold the minutes of family pictures around it."""
    held = held_chains(_rows((0, False), (2, True), (4, False)))

    assert held == {}


def test_a_gap_over_the_window_starts_a_new_chain():
    """Six minutes is not five: the two flagged captures no longer share a run."""
    held = held_chains(_rows((0, True), (6, True), (8, False)))

    assert held == {}


def test_a_chain_broken_by_the_window_keeps_its_own_dense_half():
    held = held_chains(_rows((0, True), (2, True), (4, False), (11, False), (13, False)))

    assert set(held) == {"a0", "a1", "a2"}
    assert held["a2"].size == 3 and held["a2"].swept_in


def test_the_rows_need_no_order():
    forwards = held_chains(_rows((0, True), (2, True), (4, False)))
    backwards = held_chains(list(reversed(_rows((0, True), (2, True), (4, False)))))

    assert forwards == backwards
