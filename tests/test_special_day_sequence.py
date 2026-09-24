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


def test_without_a_model_days_are_found_from_their_facts_alone(monkeypatch):
    """The NAS tier (`editorial.reader: rules`, no model) discovers days with no text call.

    A day loud on one fact is kept: the camp was spent 67 km from home, and the busy day at
    home clears the measured active-hours bar. No reader tells the cat on the sofa apart from
    an occasion; that is what the model tier adds.
    """
    assets, captions = _library()

    # WHY: the text model is the boundary; the no-model tier must never reach it.
    def no_model(*_a, **_k):
        pytest.fail("the no-model tier asked a model")

    monkeypatch.setattr("immich_memories.analysis.special_day_sequence._read", no_model)
    monkeypatch.setattr("immich_memories.automation.special_day_scan.ask_if_special", no_model)

    found = scan_year(assets, llm_config=None, home=HOME_AT, captions=captions, reader="rules")

    assert {d.day: d.what for d in found} == {
        HOME.date(): "a long day, 9 active hours",
        CAMP.date(): "a day away from home",
    }
    assert {d.day: d.title for d in found} == {
        HOME.date(): "A day in Someplace",
        CAMP.date(): "A day in Hastière",
    }


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
