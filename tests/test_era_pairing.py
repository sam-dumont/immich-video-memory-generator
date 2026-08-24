"""Pairing the same place across two eras, away from home.

Measured on a real library: about a third of the older era has a partner in the
recent one within 500 m, and roughly seven in ten of those pairs sit in a single
cluster — home. Home is the strongest then-and-now a library holds, but every
asset there shares one coordinate, so distance cannot tell the kitchen from the
garden. That half needs scene analysis and is not this.

This is the away half: places visited in both eras, where distance is the whole
signal and means what it says.
"""

from __future__ import annotations

from datetime import datetime

from immich_memories.analysis.era_pairing import PlacedMoment, pair_across_eras
from immich_memories.timeperiod import DateRange

NOW = DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 12, 31, 23, 59, 59))
THEN = DateRange(start=datetime(2016, 1, 1), end=datetime(2016, 12, 31, 23, 59, 59))
ERAS = [NOW, THEN]

# Three places far enough apart that nothing pairs across them.
HOME = (50.8500, 4.3500)
COAST = (51.2200, 2.9200)
HILLS = (50.4500, 5.9000)


def _at(asset_id: str, year: int, place: tuple[float, float], jitter: float = 0.0):
    return PlacedMoment(
        asset_id=asset_id,
        when=datetime(year, 6, 1, 12, 0, 0),
        latitude=place[0] + jitter,
        longitude=place[1],
    )


def _home_block(prefix: str, year: int, count: int) -> list[PlacedMoment]:
    """Enough material at one address to be the dominant cluster."""
    return [_at(f"{prefix}{i}", year, HOME, jitter=i * 0.00002) for i in range(count)]


def test_a_place_visited_in_both_eras_pairs() -> None:
    moments = [
        *_home_block("h", 2016, 6),
        *_home_block("H", 2026, 6),
        _at("old-coast", 2016, COAST),
        _at("new-coast", 2026, COAST),
        _at("old-hills", 2016, HILLS),
        _at("new-hills", 2026, HILLS),
    ]

    pairs = pair_across_eras(moments, ERAS, min_pairs=1)

    assert {(p.earlier_id, p.later_id) for p in pairs} == {
        ("old-coast", "new-coast"),
        ("old-hills", "new-hills"),
    }


def test_home_is_excluded_however_much_of_the_library_it_holds() -> None:
    """Distance cannot discriminate inside one address, so it must not try.

    Every asset at home shares a coordinate. Pairing on proximity there would
    match the kitchen to the garden and present it as a find.
    """
    moments = [
        *_home_block("h", 2016, 8),
        *_home_block("H", 2026, 8),
        _at("old-coast", 2016, COAST),
        _at("new-coast", 2026, COAST),
    ]

    pairs = pair_across_eras(moments, ERAS, min_pairs=1)

    assert [(p.earlier_id, p.later_id) for p in pairs] == [("old-coast", "new-coast")]


def test_a_place_seen_in_only_one_era_does_not_pair() -> None:
    """Half a pair is not a then-and-now."""
    moments = [
        *_home_block("h", 2016, 6),
        *_home_block("H", 2026, 6),
        _at("old-coast", 2016, COAST),
        _at("new-hills", 2026, HILLS),
    ]

    assert pair_across_eras(moments, ERAS, min_pairs=1) == []


def test_each_moment_is_used_at_most_once() -> None:
    """Two 'now' shots of one spot cannot both pair with the same 'then'."""
    moments = [
        *_home_block("h", 2016, 6),
        *_home_block("H", 2026, 6),
        _at("old-coast", 2016, COAST),
        _at("new-coast-a", 2026, COAST),
        _at("new-coast-b", 2026, COAST, jitter=0.0001),
    ]

    pairs = pair_across_eras(moments, ERAS, min_pairs=1)

    assert len(pairs) == 1
    assert pairs[0].earlier_id == "old-coast"


def test_too_few_pairs_refuses_rather_than_offering_a_thin_one() -> None:
    """A mode that found one pair should decline, not ship a token gesture."""
    moments = [
        *_home_block("h", 2016, 6),
        *_home_block("H", 2026, 6),
        _at("old-coast", 2016, COAST),
        _at("new-coast", 2026, COAST),
    ]

    assert pair_across_eras(moments, ERAS, min_pairs=2) == []


def test_a_memory_with_one_era_cannot_pair() -> None:
    moments = [*_home_block("H", 2026, 6), _at("new-coast", 2026, COAST)]

    assert pair_across_eras(moments, ERAS, min_pairs=1) == []


def test_the_same_input_pairs_the_same_way_every_time() -> None:
    """Ties are broken on asset id, the lesson #571 paid for.

    Two candidates at an identical distance would otherwise be ordered by
    whatever the grouping produced, which is not stable across processes.
    """
    moments = [
        *_home_block("h", 2016, 6),
        *_home_block("H", 2026, 6),
        _at("old-a", 2016, COAST),
        _at("old-b", 2016, COAST),
        _at("new-a", 2026, COAST),
        _at("new-b", 2026, COAST),
    ]

    first = pair_across_eras(moments, ERAS, min_pairs=1)
    again = pair_across_eras(list(reversed(moments)), ERAS, min_pairs=1)

    assert first == again
