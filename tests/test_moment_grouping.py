"""A day is parallel threads, not a timeline.

Measured on a real day: a circuit at 16:37, home at 16:49, the circuit again at
18:05 — 120km apart, twelve minutes. Two devices, two people, one library. Group
that by time alone and you get a story neither of them had.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.moment_grouping import moments_by_time_and_place

MIDDAY = datetime(2024, 6, 4, 12, 0, tzinfo=UTC)
CIRCUIT = (50.437, 5.971)
HOME = (50.878, 4.326)


def _asset(name: str, minutes: int, where: tuple[float, float] | None = CIRCUIT):
    exif = None
    if where is not None:
        exif = SimpleNamespace(latitude=where[0], longitude=where[1])
    return SimpleNamespace(
        id=name, file_created_at=MIDDAY + timedelta(minutes=minutes), exif_info=exif
    )


def test_close_in_time_and_place_is_one_moment() -> None:
    group = [_asset("a", 0), _asset("b", 2), _asset("c", 5)]
    assert [[a.id for a in m] for m in moments_by_time_and_place(group)] == [["a", "b", "c"]]


def test_a_gap_in_time_starts_a_new_moment() -> None:
    group = [_asset("a", 0), _asset("b", 90)]
    assert [[a.id for a in m] for m in moments_by_time_and_place(group)] == [["a"], ["b"]]


def test_far_apart_at_the_same_time_is_two_moments_not_one() -> None:
    """The finding: someone else was somewhere else, at the same time."""
    group = [_asset("circuit", 0), _asset("home", 3, HOME), _asset("circuit2", 6)]
    moments = moments_by_time_and_place(group)
    assert [[a.id for a in m] for m in moments] == [["circuit", "circuit2"], ["home"]]


def test_an_asset_without_a_place_joins_the_moment_it_sits_in() -> None:
    """Most libraries have some assets with no GPS; they must not all cluster."""
    group = [_asset("a", 0), _asset("b", 2, None), _asset("c", 4)]
    assert [[a.id for a in m] for m in moments_by_time_and_place(group)] == [["a", "b", "c"]]


def test_travelling_is_one_thread_when_the_time_allows_it() -> None:
    """A device that drove there is not a second person: hours, not minutes."""
    group = [_asset("home", 0, HOME), _asset("circuit", 180)]
    assert len(moments_by_time_and_place(group)) == 2
