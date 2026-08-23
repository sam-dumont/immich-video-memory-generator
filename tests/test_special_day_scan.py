"""Scanning a library's years for the days worth resurfacing.

The scan decides what to skip before it decides what to keep, so a wrong
answer about home is not a smaller catalogue — it is an empty one, reported
as a success.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from immich_memories.api.models import AssetType
from immich_memories.automation.special_day_scan import (
    DiscoveredDay,
    anniversaries_due,
    scan_year,
)
from immich_memories.cli.special_days_cmd import (
    _homebase,
    _load_catalogue,
    _write_catalogue,
    _year_of_assets,
    _years_in,
)
from immich_memories.config_models import AnalysisConfig, TripsConfig


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


class _Library:
    """An Immich that pages, and can be made to refuse."""

    def __init__(self, assets: list, page_size: int = 1000, *, refuse: bool = False) -> None:
        self.assets = assets
        self.page_size = page_size
        self.refuse = refuse
        self.calls = 0

    def search_metadata(self, *, taken_after, taken_before, page=1, size=1000, **_kwargs):
        self.calls += 1
        if self.refuse:
            msg = "401 Unauthorized"
            raise RuntimeError(msg)
        # The search API serializes a bare date upper bound as the END of that
        # day, so the window is closed at both ends. A double that compares it
        # exclusively encodes a contract the server does not honour.
        window = [a for a in self.assets if taken_after <= a.file_created_at.date() <= taken_before]
        start = (page - 1) * self.page_size
        chunk = window[start : start + self.page_size]
        more = len(window) > start + self.page_size
        return SimpleNamespace(
            all_assets=chunk, next_page=str(page + 1) if more else None, total=len(window)
        )


def _on(year: int, month: int, day: int, index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"a-{year}{month:02d}{day:02d}-{index:02d}",
        file_created_at=datetime(year, month, day, 12, index % 60, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def test_a_month_is_read_past_its_first_page() -> None:
    """The densest months are exactly the ones this is looking for.

    A single unpaginated query truncated them at the page size, so the days
    worth finding were the ones most likely to be cut off.
    """
    library = _Library([_on(2019, 6, 12, i) for i in range(5)], page_size=2)

    found = _year_of_assets(library, 2019)

    assert len(found) == 5


def test_the_twenty_ninth_of_february_is_inside_the_year() -> None:
    """February was hardcoded to 28 days and March began on the 1st.

    A leap day fell in the gap between the two queries, which made an event
    on one structurally impossible to discover.
    """
    library = _Library([_on(2020, 2, 29, i) for i in range(3)])

    found = _year_of_assets(library, 2020)

    assert [a.id for a in found] == ["a-20200229-00", "a-20200229-01", "a-20200229-02"]


def test_no_day_of_the_month_falls_between_two_queries() -> None:
    """Each month ended on its last day at midnight, so that day was lost."""
    library = _Library([_on(2019, 1, 31), _on(2019, 4, 30), _on(2019, 12, 31)])

    found = _year_of_assets(library, 2019)

    assert len(found) == 3, "a month's last day belongs to that month"


def test_a_library_that_refuses_every_query_is_not_an_empty_year() -> None:
    """A bad key failed all twelve queries while the command printed success."""
    library = _Library([], refuse=True)

    with pytest.raises(RuntimeError, match="2019"):
        _year_of_assets(library, 2019)


def test_a_scan_that_found_nothing_leaves_a_good_catalogue_alone(tmp_path) -> None:
    """The write was unconditional and `found` started empty.

    Immich unreachable plus twelve swallowed errors put [] over twenty years
    of scanning, and reported success doing it.
    """
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(json.dumps([{"day": "2014-12-31", "title": "A day"}]))

    _write_catalogue(catalogue, [], rescan=False)

    assert json.loads(catalogue.read_text()) == [{"day": "2014-12-31", "title": "A day"}]


def test_an_explicit_rescan_may_empty_the_catalogue(tmp_path) -> None:
    """Refusing to write is a guard against accidents, not a lock."""
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(json.dumps([{"day": "2014-12-31", "title": "A day"}]))

    _write_catalogue(catalogue, [], rescan=True)

    assert json.loads(catalogue.read_text()) == []


def test_a_catalogue_nobody_can_read_is_not_a_catalogue(tmp_path) -> None:
    """Half a file from an interrupted write must not stop the next run."""
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text('[{"day": "2014-12-31"')

    assert _load_catalogue(catalogue) == []


def test_the_years_already_in_the_catalogue_are_not_scanned_again(tmp_path) -> None:
    """A multi-hour scan that died at year twelve should not start over."""
    catalogue = [{"day": "2014-12-31"}, {"day": "2015-06-01"}, {"day": "2015-07-04"}]

    assert _years_in(catalogue) == {2014, 2015}


def test_media_the_camera_never_shot_is_gone_before_the_day_is_counted(monkeypatch) -> None:
    """A day must not clear the bar on pictures somebody else took.

    Measured on a real day the scan called special: 37 of its 223 assets were
    received or downloaded rather than shot. They pushed the day's volume and
    its active hours toward the thresholds, and they could be sampled into the
    prompt — so the model narrated pictures the owner never took.
    """
    # WHY: ask_if_special is the LLM call; a day that reaches it is a day the
    # filter failed to remove, which is the whole subject here.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(special=True, title="A day", subtitle="", what="out"),
    )

    shot = []
    for hour in range(9, 12):
        for minute in (0, 20, 40):
            asset = _asset(hour, minute)
            asset.exif_info = SimpleNamespace(city="Someplace", country="Belgium", make="Apple")
            asset.type = AssetType.IMAGE
            shot.append(asset)
    received = []
    for hour in range(12, 21):
        for minute in (0, 20, 40):
            asset = _asset(hour, minute)
            asset.exif_info = SimpleNamespace(city="Someplace", country="Belgium", make=None)
            asset.type = AssetType.IMAGE
            received.append(asset)

    # Together they clear both bars; the nine the camera shot do not, so the
    # day only becomes a candidate at all by counting the other twenty-seven.
    assert (
        scan_year(
            shot + received,
            llm_config=None,
            home=None,
            ask=1,
            analysis_config=AnalysisConfig(),
        )
        == []
    )


def test_december_does_not_reach_into_the_next_year() -> None:
    """A bare date upper bound is read as the end of that day.

    Naming the first of the next month therefore swallows it whole: a New
    Year's Day event came back in the December query, was catalogued under the
    following year, and resume then skipped scanning that year at all.
    """
    library = _Library([_on(2019, 12, 31), _on(2020, 1, 1)])

    found = _year_of_assets(library, 2019)

    assert [a.id for a in found] == ["a-20191231-00"]
