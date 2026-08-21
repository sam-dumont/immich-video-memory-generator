"""Dense days must be represented before sparse ones (#488).

Measured on a real April 2021 recap: the month's second event — 99 assets,
22% of the month, a whole coastal day — got zero clips, while three clips
scoring 0.21 from ordinary days at home took 14 of the 58.7 seconds. The
filler rule treated a 99-asset day and a 2-asset Tuesday as equally
deserving of exactly one slot, chronologically.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from immich_memories.analysis.clip_distribution import (
    _event_periods_of,
    _partition_photos_per_day,
    enforce_photo_cap,
)
from immich_memories.analysis.clip_refiner import (
    ClipRefiner,
)
from immich_memories.analysis.clip_scaler import ClipScaler
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.api.models import AssetType, Person
from tests.conftest import make_clip


def _member(day: int, index: int, score: float, *, people: bool = True) -> ClipWithSegment:
    when = datetime(2021, 4, day, 10, 0, tzinfo=UTC) + timedelta(minutes=index)
    clip = make_clip(f"a-{day:02d}-{index:03d}", duration=10.0, file_created_at=when)
    if people:
        clip.asset.people = [Person(id=f"p-{index % 3}")]
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=5.0, score=score)


def _pool() -> list[ClipWithSegment]:
    """Two dense event days and six ordinary ones — April 2021's shape."""
    pool: list[ClipWithSegment] = []
    pool += [_member(4, i, 0.50) for i in range(50)]  # the track day
    pool += [_member(9, i, 0.45) for i in range(40)]  # the coast day
    for day in (6, 12, 16, 23, 26, 30):  # ordinary days
        pool += [_member(day, i, 0.21) for i in range(2)]
    return pool


def _refiner() -> ClipRefiner:
    config = PipelineConfig(target_clips=14, avg_clip_duration=5.0)
    return ClipRefiner(config, ClipScaler())


class TestDenseDaysAreServedFirst:
    def test_both_events_are_covered_before_ordinary_days(self):
        pool = _pool()
        fillers = _refiner()._ensure_temporal_coverage([], pool, set())

        days = [c.clip.asset.file_created_at.day for c in fillers]

        # both events, before any ordinary day — two clips each, so four slots
        assert set(days[:4]) == {4, 9}, f"events must be served first, got {days[:6]}"

    def test_an_event_earns_more_than_one_clip(self):
        """A day holding a quarter of the pool deserves more than a Tuesday."""
        pool = _pool()
        fillers = _refiner()._ensure_temporal_coverage([], pool, set())

        from collections import Counter

        per_day = Counter(c.clip.asset.file_created_at.day for c in fillers)

        assert per_day[4] >= 2
        assert per_day[9] >= 2

    def test_ordinary_days_still_get_their_filler(self):
        """The recap is still a month, not a highlight reel of two days."""
        pool = _pool()
        fillers = _refiner()._ensure_temporal_coverage([], pool, set())

        ordinary = {c.clip.asset.file_created_at.day for c in fillers} - {4, 9}

        assert len(ordinary) >= 4, f"filler days were crowded out: {ordinary}"

    def test_a_flat_pool_is_unchanged(self):
        """No event, no special treatment — one clip per period as before."""
        from collections import Counter

        flat = [_member(day, i, 0.4) for day in (2, 6, 12, 16) for i in range(3)]
        fillers = _refiner()._ensure_temporal_coverage([], flat, set())

        per_day = Counter(c.clip.asset.file_created_at.day for c in fillers)

        assert set(per_day) == {2, 6, 12, 16}
        assert all(n == 1 for n in per_day.values()), per_day


class TestCoverageSurvivesTheTruncation:
    """The no-favorites branch computed temporal coverage, appended it, then
    returned `selected[:target_count]` — keeping the original top scorers and
    discarding every coverage clip. Measured on April 2021 (a month with zero
    favorites): the coverage step added a clip from the 99-asset coastal day
    and the very next line threw it away (#488)."""

    def test_a_covered_event_day_survives_to_the_output(self):
        pool: list[ClipWithSegment] = []
        # ten quiet days that score high — they fill target_count on their own
        for day in range(10, 20):
            pool += [_member(day, 0, 0.90)]
        # the event: lots of material, scoring lower than the quiet days
        pool += [_member(4, i, 0.45) for i in range(40)]

        selected = _refiner().select_clips_distributed_by_date(pool, target_count=8)
        days = {c.clip.asset.file_created_at.day for c in selected}

        assert 4 in days, f"the event day was truncated away: {sorted(days)}"

    def test_high_scorers_keep_most_of_the_room(self):
        """Coverage earns its place; it does not take over the memory."""
        pool: list[ClipWithSegment] = []
        for day in range(10, 20):
            pool += [_member(day, 0, 0.90)]
        pool += [_member(4, i, 0.45) for i in range(40)]

        selected = _refiner().select_clips_distributed_by_date(pool, target_count=8)
        high = sum(1 for c in selected if c.score >= 0.9)

        assert high >= len(selected) // 2, f"only {high} of {len(selected)} clips are high scorers"


def _photo(day: int, index: int, score: float, *, people: bool = True) -> ClipWithSegment:
    clip = _member(day, index, score, people=people).clip
    clip.asset.type = AssetType.IMAGE
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=score)


def test_event_day_keeps_more_photos_than_an_ordinary_day() -> None:
    """A flat per-day cap erased the very density selection needs.

    November 2019's busiest day — 129 Live Photos inside one hour — reached
    selection holding two clips, identical to a day with two snapshots.
    """
    pool = [_photo(10, i, 0.30) for i in range(75)]
    for day in (2, 3, 6, 8, 12, 14, 21, 23, 27):
        pool += [_photo(day, i, 0.40) for i in range(4)]

    kept, overflow = _partition_photos_per_day(pool)

    on_event_day = [c for c in kept if c.clip.asset.file_created_at.day == 10]
    on_ordinary_day = [c for c in kept if c.clip.asset.file_created_at.day == 27]
    assert len(on_event_day) > len(on_ordinary_day)
    assert overflow, "the rest stays available for duration backfill"


def test_flat_month_gives_every_day_the_same_cap() -> None:
    """Contrast, not an absolute share — no day in a flat month is an event."""
    pool = [_photo(day, i, 0.30) for day in range(1, 20) for i in range(4)]

    kept, _overflow = _partition_photos_per_day(pool)

    per_day = Counter(c.clip.asset.file_created_at.day for c in kept)
    assert set(per_day.values()) == {2}


def test_photo_cap_keeps_the_clips_selection_protected() -> None:
    """Coverage the scaler honours must survive the photo cap too.

    Measured: 13 clips went into the cap with three from the month's busiest
    day and came out with none, because the cap ranked them as ordinary
    photos and they scored below the rest.
    """
    videos = [_member(4, i, 0.60) for i in range(6)]
    protected = [_photo(10, i, 0.10) for i in range(3)]
    others = [_photo(20, i, 0.90) for i in range(6)]

    kept = enforce_photo_cap(
        videos + protected + others,
        max_ratio=0.25,
        protected_ids={c.clip.asset.id for c in protected},
    )

    kept_ids = {c.clip.asset.id for c in kept}
    assert all(c.clip.asset.id in kept_ids for c in protected)


def test_a_dense_day_with_nobody_in_it_is_not_an_event() -> None:
    """Volume is not significance.

    The densest day of a real November was 130 photos of an empty apartment —
    a property viewing. Immich recognised a person in 0% of them, against
    14-51% on every genuine event day, so density alone promoted a catalogue
    over the month's real days.
    """
    viewing = [_photo(10, i, 0.30, people=False) for i in range(75)]
    ordinary = [_photo(day, i, 0.40) for day in (2, 3, 6, 8, 12, 27) for i in range(4)]

    events = _event_periods_of(viewing + ordinary)

    assert not events, "a people-free burst is a catalogue, not a memory"
