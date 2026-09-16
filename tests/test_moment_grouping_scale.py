"""Metadata grouping must retire history that can no longer join a current event."""

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.moment_grouping import group_by_time_and_place


def test_separated_events_do_not_revisit_every_previous_timestamp():
    timestamp_reads = 0
    start = datetime(2030, 1, 1, tzinfo=UTC)

    class Source:
        def __init__(self, index):
            self.id = str(index)
            self.instant = start + timedelta(hours=2 * index)

        @property
        def taken_at(self):
            # Count the work through the input protocol instead of imposing
            # a machine-dependent stopwatch limit on the public grouper.
            nonlocal timestamp_reads
            timestamp_reads += 1
            return self.instant

    sources = [Source(index) for index in range(1000)]
    groups = group_by_time_and_place(sources, window_minutes=90)

    assert [group[0].id for group in groups] == [source.id for source in sources]
    assert all(len(group) == 1 for group in groups)
    assert timestamp_reads < 10 * len(sources)
