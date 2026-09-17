"""Every catalogued day says which scan judged it, and a period can be rebuilt.

One real catalogue held 26 rows in three shapes, written by three different
versions of the scan over about a year, with nothing on any of them to say
which. The owner's only way to clean it was to edit JSON by hand.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from immich_memories.analysis.special_day import PROMPT_VERSION
from immich_memories.automation.catalogue import (
    entries_from,
    judged_by_this_build,
    record_for,
    rows_outside,
)
from immich_memories.automation.special_day_scan import DiscoveredDay


def _a_busy_day() -> list:
    """Assets enough to clear the scan's structural bar: 20 pictures over 6 hours."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            id=f"a-{hour}-{minute}",
            file_created_at=datetime(2024, 8, 9, hour, minute, tzinfo=UTC),
            exif_info=SimpleNamespace(city="Somewhere", country="Belgium"),
            people=[],
        )
        for hour in range(9, 18)
        for minute in (0, 20, 40)
    ]


def _a_day(day: date = date(2024, 8, 9), **kwargs) -> DiscoveredDay:
    return DiscoveredDay(
        day=day,
        title="Music by the lake",
        subtitle="",
        what="a festival",
        photos=42,
        window=None,
        active_hours=9,
        run_start=datetime(day.year, day.month, day.day, 9, tzinfo=UTC),
        run_end=datetime(day.year, day.month, day.day, 18, tzinfo=UTC),
        **kwargs,
    )


def test_a_day_the_scan_judged_says_which_scan_judged_it(tmp_path, monkeypatch) -> None:
    """Both stamps, written by the scan and read back off the row."""
    from immich_memories import __version__
    from immich_memories.analysis.special_day import SpecialDay
    from immich_memories.automation.special_day_scan import scan_year

    # WHY: ask_if_special is the live model call; the stamp on its result is the subject.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SpecialDay(special=True, title="Music by the lake", what="a festival"),
    )
    path = tmp_path / "special-days.json"
    path.write_text(
        json.dumps(
            [record_for(day) for day in scan_year(_a_busy_day(), llm_config=None, home=None)]
        )
    )

    entry = entries_from(path)[0]

    assert entry.prompt_version == PROMPT_VERSION
    assert entry.app_version == __version__
    assert judged_by_this_build(entry)


def test_a_row_from_an_older_scan_reads_as_stale(tmp_path) -> None:
    """Rows written before #1065 have no stamp at all, and there is no rescuing them."""
    path = tmp_path / "special-days.json"
    path.write_text(json.dumps([{"day": "2024-08-09", "title": "A pleasant afternoon"}]))

    entry = entries_from(path)[0]

    assert entry.prompt_version == ""
    assert not judged_by_this_build(entry)


def test_a_day_nobody_could_judge_is_recorded_as_unjudged_not_as_a_day(tmp_path) -> None:
    """Recorded so the next scan knows it was reached, never offered as a memory."""
    path = tmp_path / "special-days.json"
    row = record_for(_a_day(judged=False))
    path.write_text(json.dumps([row]))

    assert row["unjudged"] == "2024-08-09"
    assert "day" not in row
    assert entries_from(path) == []


def test_rebuilding_a_period_keeps_everything_outside_it() -> None:
    """The destructive path is bounded by the years it was asked to re-scan."""
    rows = [
        record_for(_a_day(date(2019, 6, 1))),
        record_for(_a_day(date(2024, 8, 9))),
        record_for(_a_day(date(2024, 12, 25), judged=False)),
        {"scanned": 2024},
        {"scanned": 2019},
    ]

    kept, dropped = rows_outside(rows, 2024, 2024)

    assert dropped == 3
    assert kept == [rows[0], rows[4]]
