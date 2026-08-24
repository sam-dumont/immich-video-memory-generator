"""The sweep's per-run summary has to account for photo work as well as video.

A pre-2019 month is almost all stills. Its clip caches genuinely do nothing,
so the summary read "0 cached / 0 analyzed" while the photo look cache served
every photo in the month — the one line reporting on a run said no work
happened on the run that was entirely cache hits.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sweep_contact_sheets  # noqa: E402


def test_photo_cache_hits_are_counted() -> None:
    """The photo look cache reports itself; nothing was reading it."""
    log = "Photo score cache: 37 hits, 2 misses\n"

    counts = sweep_contact_sheets.cache_activity(log)

    assert counts["cached_photos"] == 37
    assert counts["scored_photos"] == 2


def test_the_summary_says_which_medium_each_count_is_about() -> None:
    """Unlabelled counts read as "nothing happened" on a photo-era month."""
    counts = {
        "cached_clips": 0,
        "analyzed_clips": 0,
        "cached_photos": 37,
        "scored_photos": 2,
    }

    line = sweep_contact_sheets.summarise(480.0, counts)

    assert line == "480s, video: 0 cached / 0 analyzed, photos: 37 cached / 2 scored"


def test_a_month_that_only_scored_photos_is_a_cold_run() -> None:
    """It landed in "warm" because zero clips is never more than zero clips."""
    timing = {
        "cached_clips": 0,
        "analyzed_clips": 0,
        "cached_photos": 0,
        "scored_photos": 30,
    }

    assert sweep_contact_sheets.was_cold(timing) is True


def test_timings_written_before_photo_counts_existed_still_classify() -> None:
    """The sweep appends to timings.json across runs, so old rows come back."""
    timing = {"cached_clips": 4, "analyzed_clips": 41}

    assert sweep_contact_sheets.was_cold(timing) is True
