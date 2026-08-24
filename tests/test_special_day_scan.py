"""Scanning a library's years for the days worth resurfacing.

The scan decides what to skip before it decides what to keep, so a wrong
answer about home is not a smaller catalogue — it is an empty one, reported
as a success.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from immich_memories.api.models import AssetType
from immich_memories.automation.special_day_scan import (
    DiscoveredDay,
    anniversaries_due,
    scan_year,
)
from immich_memories.cli.special_days_cmd import (
    _entries_in,
    _homebase,
    _load_catalogue,
    _write_catalogue,
    _year_of_assets,
    _years_in,
)
from immich_memories.config_models_analysis import AnalysisConfig
from immich_memories.config_models_automation import TripsConfig


def _asset(hour: int, minute: int = 0, day: int = 13) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"a-{day:02d}-{hour:02d}-{minute:02d}",
        file_created_at=datetime(2021, 4, day, hour, minute, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def _a_full_day() -> list[SimpleNamespace]:
    return [_asset(h, m) for h in range(9, 17) for m in (0, 20, 40)]


# Synthetic coordinates: a home, and somewhere about 67 km due north of it.
_HOME = (50.0, 4.0)
_AWAY = (50.6, 4.0)


def _christmas_day(where: tuple[float, float]) -> list[SimpleNamespace]:
    """A full day of pictures on a fixed holiday, all taken in one place."""
    return [
        SimpleNamespace(
            id=f"a-1225-{hour:02d}-{minute:02d}",
            file_created_at=datetime(2021, 12, 25, hour, minute, tzinfo=UTC),
            exif_info=SimpleNamespace(
                city="Someplace",
                state=None,
                country="Belgium",
                latitude=where[0],
                longitude=where[1],
            ),
            people=[],
        )
        for hour in range(9, 17)
        for minute in (0, 20, 40)
    ]


def _home_config() -> TripsConfig:
    return TripsConfig(homebase_latitude=_HOME[0], homebase_longitude=_HOME[1])


class TestAHolidayIsOnlySkippedWhenTheDayLooksLikeOne:
    """The date alone was enough to drop a day, and dates collide.

    A track-day excursion fell on a holiday: 133 photographs, seven active
    hours, 67 km from home. It would have ranked third in its year and never
    reached the ranking at all, because the calendar said the holiday memory
    already had that date covered.
    """

    @pytest.fixture(autouse=True)
    def _a_model_that_says_yes(self, monkeypatch) -> None:
        # WHY: ask_if_special is the LLM call; which days reach it is the subject.
        monkeypatch.setattr(
            "immich_memories.automation.special_day_scan.ask_if_special",
            lambda *_a, **_k: SimpleNamespace(
                special=True, title="A day", subtitle="", what="out", window=None
            ),
        )

    def test_a_holiday_spent_away_from_home_still_reaches_the_model(self) -> None:
        found = scan_year(
            _christmas_day(_AWAY),
            llm_config=None,
            home=_HOME,
            ask=1,
            trips_config=_home_config(),
        )

        assert [d.day for d in found] == [date(2021, 12, 25)]

    def test_a_holiday_kept_at_home_is_still_skipped(self) -> None:
        """The holiday memory does cover this one, which is why the skip exists."""
        found = scan_year(
            _christmas_day(_HOME),
            llm_config=None,
            home=_HOME,
            ask=1,
            trips_config=_home_config(),
        )

        assert found == []

    def test_a_holiday_that_recorded_no_location_is_skipped(self) -> None:
        """Nothing contradicts the holiday, so the date stands as it always did."""
        unplaced = _christmas_day(_HOME)
        for asset in unplaced:
            asset.exif_info.latitude = None
            asset.exif_info.longitude = None

        found = scan_year(unplaced, llm_config=None, home=_HOME, ask=1, trips_config=_home_config())

        assert found == []


def test_trip_detection_runs_with_the_thresholds_this_library_configured(monkeypatch) -> None:
    """The scan called detect_trips on its defaults while every other caller
    passed the configured ones, so a library that had tuned what counts as a
    trip got a different answer from this command than from the rest.
    """
    seen: dict = {}

    # WHY: detect_trips reverse-geocodes and clusters; what it was asked for is
    # the whole subject of this test.
    def _record(assets, home_lat, home_lon, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("immich_memories.automation.special_day_scan.detect_trips", _record)
    # WHY: ask_if_special is the LLM call, and no day needs to reach it here.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(
            special=False, title="", subtitle="", what="", window=None
        ),
    )

    scan_year(
        _a_full_day(),
        llm_config=None,
        home=_HOME,
        ask=1,
        trips_config=TripsConfig(
            homebase_latitude=_HOME[0],
            homebase_longitude=_HOME[1],
            min_distance_km=120,
            min_duration_days=4,
            max_gap_days=1,
        ),
    )

    assert seen["min_distance_km"] == 120
    assert seen["min_duration_days"] == 4
    assert seen["max_gap_days"] == 1


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
        lambda *_a, **_k: SimpleNamespace(
            special=True, title="A day", subtitle="", what="out", window=None
        ),
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
    def _capture(prompt, _llm_config, _timeout, images, thinking=False):
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


def test_the_catalogue_records_how_long_the_day_stayed_awake(monkeypatch) -> None:
    """Active hours is the signal that separates an occasion from a busy hour.

    The scan measured it to decide whether to ask about the day at all, then
    threw the number away, so nothing downstream could size a memory by how
    long the day actually ran.
    """
    # WHY: ask_if_special is the LLM call; the day's own hours are the subject.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(
            special=True, title="A long evening out", subtitle="", what="out", window=None
        ),
    )

    evening = [_asset(h, m) for h in range(17, 23) for m in (0, 15, 30, 45)]

    found = scan_year(evening, llm_config=None, home=None, ask=1)

    assert [d.active_hours for d in found] == [6]


def test_a_run_that_crossed_midnight_is_measured_as_the_one_night_it_was(monkeypatch) -> None:
    """The catalogue keys a day by the date its run began, and runs run late.

    A night that starts at nine and ends at three belongs to the evening it
    started, and the calendar disagrees twice over: the first date holds
    three of its six hours, and a memory scoped to that date ends at
    midnight, halfway through the thing that happened.
    """
    # WHY: ask_if_special is the LLM call; where the night's hours fall is the subject.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SimpleNamespace(
            special=True, title="A long evening out", subtitle="", what="out", window=None
        ),
    )
    evening = [_asset(h, m, day=13) for h in (21, 22, 23) for m in (0, 15, 30, 45)]
    small_hours = [_asset(h, m, day=14) for h in (0, 1, 2) for m in (0, 15, 30, 45)]

    found = scan_year(evening + small_hours, llm_config=None, home=None, ask=1)

    assert [(d.day, d.active_hours) for d in found] == [(date(2021, 4, 13), 6)]
    assert found[0].run_start == datetime(2021, 4, 13, 21, 0, tzinfo=UTC)
    assert found[0].run_end == datetime(2021, 4, 14, 2, 45, tzinfo=UTC)


def _days_due(catalogue: Path, on: str) -> str:
    from click.testing import CliRunner

    from immich_memories.cli import main
    from immich_memories.config_loader import Config

    # WHY: the CLI group loads the user's real config directory on startup.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=Config()),
    ):
        result = CliRunner().invoke(
            main,
            ["days-due", "--on", on, "--catalogue", str(catalogue)],
            catch_exceptions=False,
        )
    return result.output


def test_the_hours_survive_the_trip_through_the_catalogue(tmp_path) -> None:
    """A number the scan measured and only the scan can see is not a catalogue."""
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(
        json.dumps(
            [
                {
                    "day": "2015-06-12",
                    "title": "A long evening out",
                    "subtitle": "",
                    "what": "out",
                    "photos": 133,
                    "window": None,
                    "active_hours": 6,
                }
            ]
        )
    )

    assert "6h" in _days_due(catalogue, "2025-06-12")


def test_the_night_a_run_ended_survives_the_trip_through_the_catalogue(tmp_path) -> None:
    """The extent is the only record that a night ran past its own date.

    Written and never read back is the same as never having measured it, and
    what will scope a memory to this night is the pair, timezone included.
    """
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(
        json.dumps(
            [
                {
                    "day": "2015-06-12",
                    "title": "A long evening out",
                    "photos": 133,
                    "active_hours": 6,
                    "run_start": "2015-06-12T21:00:00+00:00",
                    "run_end": "2015-06-13T02:45:00+00:00",
                }
            ]
        )
    )

    entry = _entries_in(catalogue)[0]

    assert (entry.run_start, entry.run_end) == (
        datetime(2015, 6, 12, 21, 0, tzinfo=UTC),
        datetime(2015, 6, 13, 2, 45, tzinfo=UTC),
    )


def test_a_catalogue_written_before_the_hours_existed_still_loads(tmp_path) -> None:
    """A scan of twenty years is not something to ask anybody to run again.

    Entries from before these fields simply have none of them, and days-due
    has to go on printing those days rather than reporting an empty
    catalogue.
    """
    catalogue = tmp_path / "special-days.json"
    catalogue.write_text(
        json.dumps([{"day": "2015-06-12", "title": "A long evening out", "photos": 133}])
    )

    entry = _entries_in(catalogue)[0]

    assert (entry.active_hours, entry.run_start, entry.run_end) == (0, None, None)
    printed = _days_due(catalogue, "2025-06-12")
    assert "A long evening out" in printed
    assert "0h" not in printed, "a day nobody measured must not claim it lasted no time"


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


def test_a_year_is_done_only_when_it_was_scanned_through(tmp_path) -> None:
    """Finds are not the record of what was scanned.

    A year whose queries partly failed, or that was interrupted mid-scan,
    left one entry behind and was frozen half-scanned forever. A year that
    was scanned cleanly and simply held nothing left no entry, and was
    re-scanned in full on every resume — the two mistakes cancel out to
    "resume does the wrong thing either way".
    """
    catalogue = [
        {"day": "2014-12-31", "title": "A day"},
        {"scanned": 2015},
        {"scanned": 2016},
    ]

    assert _years_in(catalogue) == {2015, 2016}


def test_the_catalogue_never_defaults_into_the_working_directory():
    """Real event titles are private data; a CWD default plants them wherever
    the command happens to run — including an untracked file in a checkout."""
    from pathlib import Path

    import click

    from immich_memories.cli.special_days_cmd import register_special_day_commands

    main = click.Group()
    register_special_day_commands(main)
    for command_name in main.commands:
        out_params = [
            p for p in main.commands[command_name].params if p.name in ("out", "catalogue")
        ]
        for param in out_params:
            default = Path(param.default)
            assert default.is_absolute(), f"{command_name} --{param.name} defaults to CWD"
            assert Path.home() in default.parents
