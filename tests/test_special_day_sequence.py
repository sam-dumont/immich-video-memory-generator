"""A day is found by what it was, read beside the days around it, not by clearing a bar (#1093).

Every day below is generated. A day quiet on every axis but one (a camp day of eighteen pictures
across four hours) sits under both of the old bars; a busy ordinary day at home clears them.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.special_day import SpecialDay
from immich_memories.automation.special_day_scan import scan_year

# Synthetic coordinates: a home, and a camp about 67 km south of it.
HOME_AT = (50.8, 4.4)
CAMP_AT = (50.2, 4.4)


def _picture(when: datetime, n: int, *, city: str | None, video: float | None = None, at=None):
    lat, lon = at or (None, None)
    return SimpleNamespace(
        id=f"p-{when:%m%d%H%M}-{n}",
        file_created_at=when,
        exif_info=SimpleNamespace(
            city=city, country="Belgium" if city else None, latitude=lat, longitude=lon
        ),
        people=[],
        is_favorite=False,
        is_video=video is not None,
        duration_seconds=video,
        llm_description=None,
    )


def _day(start: datetime, *, pictures: int, hours: int, city: str | None, videos: int = 0, at=None):
    step = timedelta(hours=hours) / pictures
    return [
        _picture(start + step * n, n, city=city, video=8.0 if n < videos else None, at=at)
        for n in range(pictures)
    ]


CAMP = datetime(2021, 7, 14, 10, 0, tzinfo=UTC)
HOME = datetime(2021, 7, 3, 9, 0, tzinfo=UTC)


def _library():
    camp = _day(CAMP, pictures=18, hours=4, city="Hastière", videos=3, at=CAMP_AT)
    home = _day(HOME, pictures=60, hours=9, city="Someplace", at=HOME_AT)
    captions = {p.id: "children around a campfire at a summer camp" for p in camp}
    captions |= {p.id: "a cat asleep on a sofa" for p in home}
    return camp + home, captions


class Reader:
    # WHY: the text model is the only external boundary. It names the days whose evidence
    # line mentions a camp, the way a reader comparing the month's days would.
    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt, *_args, **_kwargs):
        self.prompts.append(prompt)
        named = [
            {"run": run, "what": "a summer camp"}
            for run, line in re.findall(r"^(R\d+) \| (.*)$", prompt, re.MULTILINE)
            if "camp" in line
        ]
        return json.dumps({"occasions": named})


@pytest.fixture
def reader(monkeypatch):
    read = Reader()
    monkeypatch.setattr("immich_memories.analysis.special_day_sequence._read", read)
    # WHY: ask_if_special is the per-day naming call; which days reach it is the subject.
    monkeypatch.setattr(
        "immich_memories.automation.special_day_scan.ask_if_special",
        lambda *_a, **_k: SpecialDay(special=False, title="Camp", what="a camp"),
    )
    return read


def test_a_day_under_every_bar_is_found_by_what_it_was(reader):
    assets, captions = _library()

    found = scan_year(assets, llm_config=None, home=None, captions=captions)

    assert [d.day for d in found] == [CAMP.date()]
    # Both days reached the reader, side by side in one reading of their month.
    assert len(reader.prompts) == 1
    assert "summer camp" in reader.prompts[0] and "cat asleep" in reader.prompts[0]


def test_an_occasion_a_film_cannot_be_cut_from_is_dropped_at_the_end(reader, caplog):
    # Three pictures in one burst read as a camp: correctly named, and unshowable.
    burst = _day(CAMP, pictures=3, hours=1, city="Hastière")
    # A photo-only camp of three episodes a morning, an afternoon and an evening apart.
    spread = [
        p
        for start in (10, 14, 19)
        for p in _day(CAMP.replace(day=21, hour=start), pictures=5, hours=1, city="Hastière")
    ]
    captions = {p.id: "children around a campfire at a summer camp" for p in burst + spread}

    with caplog.at_level("INFO"):
        found = scan_year(burst + spread, llm_config=None, home=None, captions=captions)

    assert [d.day for d in found] == [date(2021, 7, 21)]
    assert "2021-07-14 read as 'a summer camp', dropped for want of material" in caplog.text


def test_a_run_with_no_place_says_so_and_one_with_nothing_recorded_is_not_offered(reader):
    unplaced = _day(CAMP, pictures=18, hours=4, city=None)
    blank = _day(CAMP.replace(day=20), pictures=40, hours=8, city=None)
    captions = {p.id: "children around a campfire at a summer camp" for p in unplaced}

    scan_year(unplaced + blank, llm_config=None, home=None, captions=captions)

    (prompt,) = reader.prompts
    assert "place: not recorded" in prompt
    assert "Tue 20 Jul" not in prompt
    # The question comes after the evidence, never before it.
    assert prompt.index("summer camp") < prompt.index("Which of these days were occasions")


def test_a_month_the_reader_could_not_read_leaves_the_year_unscanned(monkeypatch):
    from immich_memories.automation.special_day_scan import YearNotRead

    assets, captions = _library()
    # WHY: the text model, down for this call.
    monkeypatch.setattr(
        "immich_memories.analysis.special_day_sequence._read",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no route to host")),
    )

    with pytest.raises(YearNotRead, match="2021-07"):
        scan_year(assets, llm_config=None, home=None, captions=captions)


def test_a_run_the_reader_invents_is_ignored(monkeypatch, reader):
    assets, captions = _library()
    # WHY: the text model, naming a run nobody offered it.
    monkeypatch.setattr(
        "immich_memories.analysis.special_day_sequence._read",
        lambda *_a, **_k: json.dumps({"occasions": [{"run": "R9", "what": "a wedding"}]}),
    )

    assert scan_year(assets, llm_config=None, home=None, captions=captions) == []


@pytest.fixture
def no_model(monkeypatch):
    # WHY: the text model is the boundary; the no-model tier must never reach it.
    def refuse(*_a, **_k):
        pytest.fail("the no-model tier asked a model")

    monkeypatch.setattr("immich_memories.analysis.special_day_sequence._read", refuse)
    monkeypatch.setattr("immich_memories.automation.special_day_scan.ask_if_special", refuse)


# The people file's close family, by Immich person id: roles only ever reach a line.
FAMILY = {"p-owner": "owner", "p-partner": "partner", "p-son": "son"}


def _with(pictures, *person_ids):
    for picture in pictures:
        picture.people = [SimpleNamespace(id=pid, name="A Person") for pid in person_ids]
    return pictures


def test_without_a_model_days_are_found_from_their_facts_alone(no_model):
    """The NAS tier (`editorial.reader: rules`, no model) discovers days with no text call.

    The camp was spent 67 km from home: loud on its own, with nobody in the family on it. The
    busy day at home clears the old active-hours bar with nobody in it, and the bar alone
    no longer finds a day at home (#1220).
    """
    assets, captions = _library()

    found = scan_year(
        assets,
        llm_config=None,
        home=HOME_AT,
        captions=captions,
        reader="rules",
        close_family=FAMILY,
    )

    assert {d.day: (d.what, d.title) for d in found} == {
        CAMP.date(): ("a day away from home", "A day in Hastière"),
    }


def test_without_a_model_a_long_day_at_home_with_the_family_is_found(no_model):
    party = _day(HOME, pictures=60, hours=9, city="Someplace", at=HOME_AT)
    _with(party[:30], "p-partner", "p-son")
    _with(party[30:40], "p-neighbour")

    found = scan_year(party, llm_config=None, home=HOME_AT, reader="rules", close_family=FAMILY)

    assert [d.what for d in found] == ["a long day with close family, 9 active hours"]


def test_the_line_says_which_close_family_were_there_by_role_only(reader):
    assets, captions = _library()
    camp = [a for a in assets if a.file_created_at.date() == CAMP.date()]
    _with(camp[:6], "p-son")
    _with(camp[6:9], "p-owner", "p-partner")

    scan_year(assets, llm_config=None, home=None, captions=captions, close_family=FAMILY)

    (prompt,) = reader.prompts
    assert "close family: owner, partner, son on 9 of 18 pictures" in prompt
    assert "p-son" not in prompt


@pytest.mark.parametrize(
    ("starred", "videos", "what"),
    [(3, 0, "3 favourites"), (0, 6, "a day mostly on video"), (2, 2, None)],
)
def test_without_a_model_one_loud_fact_is_enough(starred, videos, what):
    # Twelve pictures in three episodes of one afternoon at home: under the hours bar.
    day = [
        p
        for start in (12, 15, 18)
        for p in _day(HOME.replace(hour=start), pictures=4, hours=1, city="Someplace")
    ]
    for picture in day[:starred]:
        picture.is_favorite = True
    for picture in day[-videos:] if videos else []:
        picture.is_video, picture.duration_seconds = True, 8.0

    found = scan_year(day, llm_config=None, home=None, reader="rules")

    assert [d.what for d in found] == ([what] if what else [])


def _thirty_loud_days():
    """Thirty runs in one year, each loud on one fact, with strengths that rank them."""
    runs = []
    for n in range(30):
        start = datetime(2021, 1 + n // 3, 3 + (n % 3) * 9, 10, tzinfo=UTC)
        spread = [
            p
            for hour in (0, 3, 6)
            for p in _day(start + timedelta(hours=hour), pictures=4, hours=1, city="Someplace")
        ]
        kind = n % 3
        if kind == 0:  # away from home, further each time
            for p in spread:
                p.exif_info.latitude, p.exif_info.longitude = HOME_AT[0] - 0.5 - n / 100, HOME_AT[1]
        elif kind == 1:  # favourites, more each time
            for p in spread[: 3 + n // 3]:
                p.is_favorite = True
        else:  # a long family day at home
            spread = _day(start, pictures=24, hours=8, city="Someplace", at=HOME_AT)
            _with(spread[: 4 + n // 3], "p-son")
        runs.append((n, kind, spread))
    return runs


def test_without_a_model_a_year_yields_its_strongest_few(no_model):
    runs = _thirty_loud_days()
    assets = [p for _n, _k, day in runs for p in day]

    found = scan_year(
        assets, llm_config=None, home=HOME_AT, reader="rules", close_family=FAMILY, per_year=6
    )

    # Away days rank first, the furthest first; ten of them are loud that way.
    furthest = sorted((n for n, kind, _ in runs if kind == 0), reverse=True)[:6]
    expected = {runs[n][2][0].file_created_at.date() for n in furthest}
    assert {d.day for d in found} == expected
    assert {d.what for d in found} == {"a day away from home"}


def test_the_model_tier_is_not_capped_by_the_shortlist(reader):
    days = [
        _day(
            datetime(2021, 1 + n // 3, 3 + (n % 3) * 9, 10, tzinfo=UTC),
            pictures=18,
            hours=4,
            city="Hastière",
            videos=3,
        )
        for n in range(30)
    ]
    captions = {p.id: "children at a summer camp" for day in days for p in day}

    found = scan_year(
        [p for d in days for p in d], llm_config=None, home=None, captions=captions, per_year=6
    )

    assert len(found) == 30
