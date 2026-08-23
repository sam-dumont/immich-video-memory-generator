"""Two shots of one moment must not both ship, wherever they fall on the clock.

A real December cut ended with the same family group photographed twice, one
minute apart. Dedup bucketed on a fixed grid — int(epoch / window) — so
whether two shots counted as the same moment depended on which side of an
arbitrary boundary they landed: 15:55 and 15:57 collapsed, 15:54 and 15:56
did not.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from immich_memories.analysis.clip_scaler import ClipScaler, group_by_moment, is_same_moment
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from tests.conftest import make_clip


def _at(
    hour: int, minute: int, score: float = 0.5, *, is_favorite: bool = False
) -> ClipWithSegment:
    when = datetime(2019, 12, 25, hour, minute, tzinfo=UTC)
    clip = make_clip(
        f"c-{hour}{minute}", duration=5.0, file_created_at=when, is_favorite=is_favorite
    )
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=2.4, score=score)


@pytest.mark.parametrize(("first", "second"), [((15, 55), (15, 57)), ((15, 54), (15, 56))])
def test_two_minutes_apart_is_one_moment_wherever_the_grid_falls(
    first: tuple[int, int], second: tuple[int, int]
) -> None:
    kept = ClipScaler().deduplicate_temporal_clusters(
        [_at(*first, score=0.88), _at(*second, score=0.94)], time_window_minutes=5.0
    )

    assert len(kept) == 1
    assert kept[0].score == 0.94, "the better of the two survives"


def test_shots_further_apart_than_the_window_are_separate_moments() -> None:
    kept = ClipScaler().deduplicate_temporal_clusters(
        [_at(15, 54), _at(16, 10)], time_window_minutes=5.0
    )

    assert len(kept) == 2


def test_a_held_shutter_stays_one_moment_however_long_it_runs() -> None:
    """Consecutive gaps under the window chain into a single cluster."""
    burst = [_at(15, m) for m in (50, 53, 56, 59)]

    assert len(group_by_moment(burst, 5.0)) == 1


def test_backfill_can_tell_a_moment_is_already_in_the_cut() -> None:
    taken = [datetime(2019, 12, 25, 15, 54, tzinfo=UTC)]

    assert is_same_moment(datetime(2019, 12, 25, 15, 56, tzinfo=UTC), taken, 5.0)
    assert not is_same_moment(datetime(2019, 12, 25, 16, 10, tzinfo=UTC), taken, 5.0)
    assert not is_same_moment(None, taken, 5.0)


def test_exactly_one_window_apart_is_still_one_moment() -> None:
    """A 2023 hike put 08:34 and 08:39 in the same cut.

    Five minutes apart, on a five-minute window, called distinct by one second
    of arithmetic.
    """
    kept = ClipScaler().deduplicate_temporal_clusters(
        [_at(8, 34, score=0.64), _at(8, 39, score=0.48)], time_window_minutes=5.0
    )

    assert len(kept) == 1


def test_a_starred_shot_survives_a_cluster_when_the_cut_keeps_two_per_moment() -> None:
    """Keeping one per moment protects favourites; keeping several must too.

    A long memory asks for more than one clip per moment, and ranking the
    cluster on score alone puts a starred shot behind any two ordinary ones
    that happen to score better — dropping exactly what the viewer marked.
    """
    starred = _at(15, 50, score=0.5, is_favorite=True)
    good = _at(15, 52, score=0.9)
    fine = _at(15, 54, score=0.8)

    kept = ClipScaler().deduplicate_temporal_clusters(
        [starred, good, fine], time_window_minutes=5.0, keep_per_moment=2
    )

    assert len(kept) == 2
    assert [c.clip.asset.is_favorite for c in kept].count(True) == 1, (
        "the starred shot is kept, not outranked by score"
    )
    assert {c.clip.asset.id for c in kept} == {
        starred.clip.asset.id,
        good.clip.asset.id,
    }, "the free slot goes to the best of the rest"
