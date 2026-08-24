"""Two photos of the same thing are duplicates however far apart the clock says.

The temporal rule asks "were these within N minutes". A month uses 5. The
verdict named two bath photos five minutes apart in a huge pool, four shots of
one Christmas moment, and two of one boat show — all of which clear a 5-minute
window and none of which a person would call different moments.

Measured on 1,124,250 real description pairs from the cache: Jaccard over
description tokens separates them cleanly. 0.83 was the same person in the same
living room playing the same game; 0.75/0.73/0.72 the same light-green gravel
bike on the same path; 0.70 the same child in the same hallway, differing only
on a t-shirt detail.

The threshold is the knee of that curve. 0.60 collapses 33 pairs in 1.1M
(0.003%); 0.55 collapses 74, 0.50 collapses 135 — the count triples per step
below it, which is where distinct shots start merging.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from immich_memories.analysis.clip_scaler import describes_the_same_thing


def _clip(asset_id: str, description: str, minute: int = 0, score: float = 0.5):
    return SimpleNamespace(
        clip=SimpleNamespace(
            asset=SimpleNamespace(
                id=asset_id,
                is_favorite=False,
                file_created_at=datetime(2011, 8, 4, 12, minute, tzinfo=UTC),
            ),
            llm_description=description,
            llm_subjects=None,
        ),
        start_time=0.0,
        end_time=4.0,
        score=score,
        analyzed=True,
    )


def test_the_same_scene_photographed_twice_is_one_thing() -> None:
    """Beyond the temporal window, identical subject. Synthetic wording."""
    a = _clip("a", "A man is riding a light green gravel bike along a paved path", minute=0)
    b = _clip("b", "A man is riding a light green gravel bike along a paved path", minute=30)

    assert describes_the_same_thing(a, b)


def test_a_shared_setting_is_not_the_same_thing() -> None:
    """Two different events in one kitchen must both survive."""
    a = _clip("a", "A child blows out candles on a birthday cake in the kitchen")
    b = _clip("b", "Two adults are washing dishes at the kitchen sink after dinner")

    assert not describes_the_same_thing(a, b)


def test_a_clip_nobody_described_is_never_merged() -> None:
    """Unknown is not similar. Only 21.5% of segments carry descriptions."""
    a = _clip("a", None)
    b = _clip("b", None)
    c = _clip("c", "A man is riding a light green gravel bike along a paved path")

    assert not describes_the_same_thing(a, b)
    assert not describes_the_same_thing(a, c)


def test_a_wording_difference_still_counts_as_the_same_thing() -> None:
    """One scene described twice in near-identical words scores 0.80 and merges.

    Synthetic rather than a real capture: descriptions from the library name
    real people and places and do not belong in the repo.
    """
    a = _clip("a", "A cyclist rides a green gravel bicycle along a paved woodland path")
    b = _clip("b", "A cyclist rides a green gravel bicycle along a paved woodland trail")

    assert describes_the_same_thing(a, b)
