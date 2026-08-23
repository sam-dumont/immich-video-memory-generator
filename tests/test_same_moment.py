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


def test_a_month_with_nearly_enough_moments_keeps_one_clip_from_each() -> None:
    """Measured on a real December recap: eight clips, and two of them were
    the same group photographed two minutes apart.

    `ceil(target / moments)` rises above one as soon as moments is below
    target, and selection is already thinned to about the target count by the
    time dedup runs — so "a long memory may keep more than one" was every
    memory, and the same-moment rule stopped applying to any of them.
    """
    from immich_memories.analysis.clip_refiner import _clips_per_moment

    assert _clips_per_moment(target_clips=14, moments=9) == 1


def test_a_cut_with_far_fewer_moments_than_clips_still_doubles_up() -> None:
    """The case the rule exists for: 967 assets, 16 moments, a 55-clip cut.

    Thinned to one each it could not fill the runtime, and backfill put the
    rejected duplicates back by relaxing its constraints.
    """
    from immich_memories.analysis.clip_refiner import _clips_per_moment

    assert _clips_per_moment(target_clips=55, moments=16) == 4


def test_the_clips_that_make_an_event_read_as_a_day_survive_dedup() -> None:
    """A dense day is given three clips so it reads as a day, not a glimpse.

    The duration scaler and the photo cap both honour those ids. The moment
    dedup did not, so a day whose three clips fell inside one window came
    straight back down to the single glimpse (#490, #510).
    """
    event = [_at(15, 50, score=0.3), _at(15, 52, score=0.5), _at(15, 54, score=0.4)]

    kept = ClipScaler().deduplicate_temporal_clusters(
        event,
        time_window_minutes=5.0,
        protected_ids={c.clip.asset.id for c in event},
    )

    assert len(kept) == 3


def test_protection_covers_the_event_clips_and_not_what_sits_beside_them() -> None:
    """Whatever else lands in that window is still a duplicate."""
    covering = _at(15, 50, score=0.3)
    passer_by = _at(15, 52, score=0.9)

    kept = ClipScaler().deduplicate_temporal_clusters(
        [covering, passer_by],
        time_window_minutes=5.0,
        protected_ids={covering.clip.asset.id},
    )

    assert [c.clip.asset.id for c in kept] == [covering.clip.asset.id]


class TestAMomentIsRelativeToTheStoryBeingTold:
    """Five minutes is a moment in a month and a rounding error in a year.

    Measured on a real year recap: two of its thirty-nine slots went to one
    evening at a venue (71 minutes apart) and two more to one arcade (62
    minutes apart). To anybody watching, each of those is one evening.
    """

    def test_a_month_keeps_the_window_it_was_configured_with(self) -> None:
        from immich_memories.analysis.clip_distribution import moment_window_for

        assert moment_window_for(span_days=31, configured_minutes=5.0) == 5.0

    def test_a_year_widens_the_window_past_an_evening_apart(self) -> None:
        from immich_memories.analysis.clip_distribution import moment_window_for

        window = moment_window_for(span_days=365, configured_minutes=5.0)

        assert window > 71.0, "the pairs a real year recap doubled up on"

    def test_a_configured_window_is_a_floor_and_never_narrowed(self) -> None:
        """Somebody who asked for a wide window meant it."""
        from immich_memories.analysis.clip_distribution import moment_window_for

        assert moment_window_for(span_days=365, configured_minutes=240.0) == 240.0

    def test_zero_still_turns_deduplication_off(self) -> None:
        from immich_memories.analysis.clip_distribution import moment_window_for

        assert moment_window_for(span_days=365, configured_minutes=0.0) == 0.0


def test_a_starred_shot_is_not_squeezed_out_by_protected_coverage() -> None:
    """Protection takes slots; it must not take the star's.

    With the cap filled by clips coverage deliberately kept, room reaches zero
    and the ranked list is emptied — dropping the favourite the ranking exists
    to put first, and backfill can never re-admit a clip from a moment already
    in the cut.
    """
    covering = _at(15, 50, score=0.3)
    also_covering = _at(15, 52, score=0.4)
    starred = _at(15, 54, score=0.2)
    starred.clip.asset.is_favorite = True

    kept = ClipScaler().deduplicate_temporal_clusters(
        [covering, also_covering, starred],
        time_window_minutes=5.0,
        keep_per_moment=2,
        protected_ids={covering.clip.asset.id, also_covering.clip.asset.id},
    )

    assert starred.clip.asset.id in {c.clip.asset.id for c in kept}
