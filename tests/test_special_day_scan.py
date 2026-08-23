"""Scanning a library's years for the days worth resurfacing.

The scan decides what to skip before it decides what to keep, so a wrong
answer about home is not a smaller catalogue — it is an empty one, reported
as a success.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from immich_memories.automation.special_day_scan import (
    DiscoveredDay,
    anniversaries_due,
    scan_year,
)
from immich_memories.cli.special_days_cmd import _homebase
from immich_memories.config_models import TripsConfig


def _asset(hour: int, minute: int = 0, day: int = 13) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"a-{day:02d}-{hour:02d}-{minute:02d}",
        file_created_at=datetime(2021, 4, day, hour, minute, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def _a_full_day() -> list[SimpleNamespace]:
    return [_asset(h, m) for h in range(9, 17) for m in (0, 20, 40)]


def test_the_configured_homebase_is_the_one_the_scan_uses() -> None:
    """The scan read two fields that do not exist, so home was a constant.

    Anyone living away from that constant had their at-home days marked as
    trips, and the year was swallowed before a day was ever considered.
    """
    config = SimpleNamespace(trips=TripsConfig(homebase_latitude=12.5, homebase_longitude=-3.25))

    assert _homebase(config) == (12.5, -3.25)


def test_an_unset_homebase_is_not_replaced_by_a_guess() -> None:
    """Null Island is this codebase's way of saying nobody set one."""
    assert _homebase(SimpleNamespace(trips=TripsConfig())) is None


def test_without_a_homebase_the_scan_excludes_no_days(monkeypatch) -> None:
    """Nothing to be away from means nothing to skip.

    Guessing a home is worse than having none: every day at the real home
    reads as away, and the catalogue comes back empty.
    """
    # WHY: ask_if_special is the LLM call; the verdict is not what this tests.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(special=True, title="A day", subtitle="", what="out"),
    )
    # WHY: detect_trips reverse-geocodes over the network, and without a home
    # there is nothing for it to measure against — it must not run at all.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.detect_trips",
        lambda *_a, **_k: pytest.fail("trip detection ran without a homebase"),
    )

    found = scan_year(_a_full_day(), llm_config=None, home=None, ask=1)

    assert [d.day for d in found] == [date(2021, 4, 13)]


def test_the_prompt_lines_describe_the_pictures_sent_with_them(monkeypatch) -> None:
    """The prompt says "the pictures that go with these lines", so they must.

    The lines were sampled again, at a different count, from the assets whose
    thumbnails had already been drawn — so the model read one picture's time,
    place and names against another's, and the grounding filter then judged a
    title built on that.
    """
    from immich_memories.analysis import special_day
    from immich_memories.analysis.special_day import ask_if_special, sample_across_day

    day = [_asset(h, m) for h in range(9, 15) for m in (0, 30)]
    tiles = [(a, f"jpeg-{a.id}".encode()) for a in sample_across_day(day, count=6)]

    seen: dict = {}

    # WHY: the vision call is the network boundary; the prompt it is handed
    # is the whole point of the test.
    def _capture(prompt, _llm_config, _timeout, images):
        seen["prompt"], seen["thumbnails"] = prompt, images
        return '{"special": false}'

    monkeypatch.setattr(special_day, "_ask", _capture)

    ask_if_special(day, llm_config=SimpleNamespace(), thumbnails=tiles)

    import re

    times = re.findall(r"^  (\d\d:\d\d)", seen["prompt"], re.MULTILINE)
    assert times == [a.file_created_at.strftime("%H:%M") for a, _ in tiles]
    assert seen["thumbnails"] == [image for _, image in tiles]


def test_a_picture_that_failed_to_download_takes_its_line_with_it() -> None:
    """A short image list beside a full line list offsets everything after it."""
    from immich_memories.analysis.special_day import sample_across_day
    from immich_memories.automation.special_day_scan import SAMPLE_SIZE, _thumbnails_for

    day = _a_full_day()
    missing = sample_across_day(day, count=SAMPLE_SIZE)[2].id

    def _fetch(asset_id: str) -> bytes:
        if asset_id == missing:
            raise OSError("no thumbnail for this one")
        return f"jpeg-{asset_id}".encode()

    tiles = _thumbnails_for(day, _fetch)

    assert missing not in [asset.id for asset, _ in tiles]
    assert len(tiles) == SAMPLE_SIZE - 1


def _entry(day: date) -> DiscoveredDay:
    return DiscoveredDay(
        day=day, title="A day", subtitle="", what="something", photos=40, window=None
    )


def test_a_day_at_the_end_of_december_is_due_in_early_january() -> None:
    """Only the check's own year was tried, so the candidate sat 364 days away.

    The count was wrong in the same move: eleven years, for the tenth
    anniversary — and roundness is the whole appeal of arriving unannounced.
    """
    entry = _entry(date(2014, 12, 31))

    assert anniversaries_due([entry], date(2025, 1, 2)) == [(entry, 10)]


def test_a_day_at_the_start_of_january_is_due_in_late_december() -> None:
    """The same gap, crossed the other way."""
    entry = _entry(date(2015, 1, 1))

    assert anniversaries_due([entry], date(2024, 12, 30)) == [(entry, 10)]
