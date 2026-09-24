"""Scanning a library for days worth resurfacing.

Lives here rather than in a script because it is meant to run on a schedule:
the point of the catalogue is a memory nobody asked for — five years to the
day since the wedding, ten since the race — and that needs the days found in
advance, not while a video is waiting to render.

What it skips matters as much as what it finds. A holiday already has its own
memory, and every day inside a trip clears the structural bar here without
being remarkable on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from immich_memories.analysis.special_day import (
    MIN_ACTIVE_HOURS,
    MIN_PHOTOS,
    SpecialDay,
    active_hours,
    ask_if_special,
    candidate_days,
    days_covered_by_trips,
    event_window,
    pictures_inside,
    run_extent,
    window_that_holds_the_day,
)
from immich_memories.analysis.special_day_sequence import (
    MIN_FILM_SECONDS,
    filmable_seconds,
    read_in_sequence,
)
from immich_memories.analysis.special_day_title import honest_title
from immich_memories.analysis.special_event_scope import SpecialEventAdmission
from immich_memories.analysis.trip_detection import detect_trips, haversine_km
from immich_memories.config_models_analysis import AnalysisConfig
from immich_memories.config_models_automation import TripsConfig
from immich_memories.config_models_render import PhotoConfig
from immich_memories.memory_types.date_builders import KNOWN_HOLIDAYS, resolve_holiday

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDay:
    """What the scan made of one candidate day.

    Usually a day worth a memory of its own. With `judged` false it is a day
    the scan reached and could not read: the catalogue records those so a
    later run knows they were not simply missed, and no reader offers them.
    """

    day: date
    title: str
    subtitle: str
    what: str
    photos: int
    window: tuple[datetime, datetime] | None
    # How long the day stayed awake, and when it did. The run is keyed by the
    # date it began and can end on another one, so its extent is the only
    # honest scope for a memory of it — the calendar day stops at midnight.
    active_hours: int = 0
    run_start: datetime | None = None
    run_end: datetime | None = None
    event_id: str | None = None
    asset_ids: tuple[str, ...] = ()
    event_admission: SpecialEventAdmission | None = None
    judged: bool = True
    # How many of the day's pictures the recorded window holds, against `photos`.
    # Zero means a scan from before #1067 that never counted, so its window is
    # taken as written; every other row can be checked without re-fetching the
    # day. With no window the whole day is the scope, so this equals `photos`.
    window_photos: int = 0
    # Which scan produced this. Empty means a scan from before #1065, which
    # stamped nothing and asked a question a pleasant afternoon answered yes to.
    prompt_version: str = ""
    app_version: str = ""


def holidays_in(year: int, extra: Iterable[str] = ()) -> set[date]:
    """Dates a holiday memory already covers.

    Nothing is defined here: date_builders owns which holidays exist and when
    they fall, moving ones included. Adding one there is enough for it to be
    skipped here too.
    """
    covered: set[date] = set()
    for name in (*KNOWN_HOLIDAYS, *extra):
        try:
            covered.add(resolve_holiday(name, year))
        except ValueError:
            logger.debug("Not a holiday this build knows: %r", name)
    return covered


def _shot_here(assets: list, analysis_config: Any) -> list:
    """Whatever of this year the library's own camera actually made."""
    from immich_memories.analysis.source_filter import not_shot_here

    if analysis_config is None:
        return assets
    patterns = getattr(analysis_config, "exclude_filename_patterns", ())
    stills_need_a_camera = getattr(analysis_config, "exclude_stills_without_camera_exif", False)
    kept = [
        asset
        for asset in assets
        if not not_shot_here(asset, patterns=patterns, stills_need_a_camera=stills_need_a_camera)
    ]
    if len(kept) < len(assets):
        logger.info(
            "Source filter: %d of %d assets were not shot here",
            len(assets) - len(kept),
            len(assets),
        )
    return kept


def _kept_away_from_home(items: list, home: tuple[float, float], min_km: float) -> bool:
    """Did the day's located pictures happen somewhere other than home?

    The holiday memory covers the holiday as it is actually kept — at home,
    with the people who keep it. A day that merely falls on the same date and
    was spent 67 km away at a race circuit is not that holiday, and dropping
    it on the date alone lost a day that would have ranked third in its year.

    No coordinates at all is not evidence against the holiday, so those days
    stay skipped exactly as before.
    """
    away = at_home = 0
    for asset in items:
        exif = getattr(asset, "exif_info", None)
        lat = getattr(exif, "latitude", None) if exif else None
        lon = getattr(exif, "longitude", None) if exif else None
        if lat is None or lon is None:
            continue
        if haversine_km(lat, lon, *home) >= min_km:
            away += 1
        else:
            at_home += 1
    return away > at_home


def _drop_the_holidays_it_actually_was(
    candidates: dict[date, list],
    holidays: set[date],
    home: tuple[float, float] | None,
    min_km: float,
) -> dict[date, list]:
    """Keep the days whose evidence disagrees with the holiday they fall on."""
    kept: dict[date, list] = {}
    for day, items in candidates.items():
        if day not in holidays:
            kept[day] = items
        elif home and _kept_away_from_home(items, home, min_km):
            logger.info("%s falls on a holiday but was spent away from home; keeping it", day)
            kept[day] = items
        else:
            logger.info("Skipping %s: a holiday, and the day never left home", day)
    return kept


def scan_year(
    assets: list,
    *,
    llm_config: Any,
    home: tuple[float, float] | None,
    extra_holidays: Iterable[str] = (),
    analysis_config: Any = None,
    trips_config: TripsConfig | None = None,
    captions: dict[str, str] | None = None,
    judgment_cache_path: Path | None = None,
    still_seconds: float | None = None,
    reader: Literal["model", "rules"] = "model",
) -> list[DiscoveredDay]:
    """Find the days in one year's assets that were occasions, and name them.

    Every run of activity off a trip is read, a month at a time and in order, and the reader
    says which were occasions (`special_day_sequence`); no bar decides what it may see. Each
    occasion a film could be cut from is then named from its own pictures' lines. A month the
    reader could not read raises `YearNotRead` so the year is scanned again, not recorded
    half-read; the months it did read are banked and cost nothing the second time.

    With `reader="rules"` (no model configured) nothing is asked at all: a run is an
    occasion when one of its recorded facts is loud (`_occasion_by_facts`), and it is
    titled from its own place. The film floor applies the same on both tiers.

    Anything generation would throw away is removed first, so the scan judges
    the same library a memory could actually be cut from. Measured on a real
    day the scan called special: 37 of its 223 assets were received or
    downloaded rather than shot, and they counted toward the day's volume and
    its active hours and could be sampled into the prompt — so the model
    narrated pictures nobody in the library had taken.
    """
    if not assets:
        return []

    assets = _shot_here(assets, analysis_config)
    if not assets:
        return []

    trips = trips_config or TripsConfig()
    year = assets[0].file_created_at.year
    # Dates only: the trip's name is never read here, and asking for one
    # is a live request per trip for every year of the scan.
    away = (
        days_covered_by_trips(
            detect_trips(
                assets,
                *home,
                min_distance_km=trips.min_distance_km,
                min_duration_days=trips.min_duration_days,
                max_gap_days=trips.max_gap_days,
                name_locations=False,
            )
        )
        if home
        else set()
    )
    holidays = holidays_in(year, extra_holidays)

    off_trip = candidate_days(assets, away_days=away)
    candidates = _drop_the_holidays_it_actually_was(off_trip, holidays, home, trips.min_distance_km)
    occasions = _occasions(
        candidates,
        year=year,
        reader=reader,
        home=home,
        away_km=trips.min_distance_km,
        captions=captions,
        llm_config=llm_config,
        cache_path=judgment_cache_path,
    )
    logger.info(
        "%d: %d occasions, %d dates covered by trips, %d dropped as the holiday they fell on",
        year,
        len(occasions),
        len(away),
        len(off_trip) - len(candidates),
    )

    clip_seconds = (analysis_config or AnalysisConfig()).optimal_clip_duration
    stills = PhotoConfig().duration if still_seconds is None else still_seconds
    found: list[DiscoveredDay] = []
    for day, what in sorted(occasions.items()):
        items = candidates[day]
        if filmable_seconds(items, still_seconds=stills, clip_seconds=clip_seconds) < (
            MIN_FILM_SECONDS
        ):
            logger.info("%s read as %r, dropped for want of material to film", day, what)
            continue
        verdict = (
            SpecialDay(special=True, title=honest_title(items, what=what, evidence=""), what=what)
            if reader == "rules"
            else ask_if_special(
                items,
                llm_config,
                captions={a.id: captions[a.id] for a in items if captions and captions.get(a.id)},
                judgment_cache_path=judgment_cache_path,
            )
        )
        outcome = _day_from(day, items, verdict, what)
        if outcome is not None:
            found.append(outcome)
    return found


def _occasions(
    candidates: dict[date, list],
    *,
    year: int,
    reader: str,
    home: tuple[float, float] | None,
    away_km: float,
    captions: dict[str, str] | None,
    llm_config: Any,
    cache_path: Path | None,
) -> dict[date, str]:
    """The occasions among these runs and what each was: read by the model, or by the facts."""
    if reader == "rules":
        return {
            day: what
            for day, items in candidates.items()
            if (what := _occasion_by_facts(items, home, away_km))
        }
    reading = read_in_sequence(
        candidates, captions=captions, llm_config=llm_config, cache_path=cache_path
    )
    logger.info(
        "%d: %d runs read, %d with nothing recorded beyond the clock",
        year,
        reading.offered,
        reading.silent,
    )
    if reading.unread_months:
        raise YearNotRead(year, reading.unread_months)
    return reading.found


# A run loud on one of these facts is an occasion to the no-model tier. The first is the bar
# measured on labelled days; the others are the single loud axes the owner's confirmed occasions
# showed (#1093). Close family is not among them yet: the scan has no relationships to read.
_FAVOURITES_OF_AN_OCCASION = 3
_VIDEOS_OF_AN_OCCASION = 3
_VIDEO_SHARE_OF_AN_OCCASION = 0.5


def _occasion_by_facts(items: list, home: tuple[float, float] | None, away_km: float) -> str:
    """What the loudest recorded fact says this run was, or "" when none is loud."""
    hours = active_hours(items)
    if len(items) >= MIN_PHOTOS and hours >= MIN_ACTIVE_HOURS:
        return f"a long day, {hours} active hours"
    if home and _kept_away_from_home(items, home, away_km):
        return "a day away from home"
    stars = sum(1 for a in items if getattr(a, "is_favorite", False))
    if stars >= _FAVOURITES_OF_AN_OCCASION:
        return f"{stars} favourites"
    videos = sum(1 for a in items if getattr(a, "is_video", False))
    if videos >= _VIDEOS_OF_AN_OCCASION and videos >= _VIDEO_SHARE_OF_AN_OCCASION * len(items):
        return "a day mostly on video"
    return ""


class YearNotRead(RuntimeError):
    """Some months of a year could not be read; the year is scanned again rather than kept."""

    def __init__(self, year: int, months: list[str]) -> None:
        super().__init__(f"{year}: {len(months)} month(s) could not be read ({', '.join(months)})")


def _day_from(day: date, items: list, verdict: Any, what: str = "") -> DiscoveredDay | None:
    """One candidate day's row, or nothing when there is nothing honest to write.

    A title, not just something written about the day. Every reader of the
    catalogue falls back to `what` when the title is empty, so an entry with no
    title is how the day's own description — "Six images captured between 07:32
    and 16:06, tracing a route from weathered apar" — ended up on a card. The
    ask already offers the day's place or its `what` where either can carry a
    title; nothing left after that means nothing truthful to call the day.
    """
    from immich_memories import __version__
    from immich_memories.analysis.special_day_sequence import SCAN_VERSION

    started, ended = run_extent(items) or (None, None)
    if not verdict.judged:
        return DiscoveredDay(
            day=day,
            title="",
            subtitle="",
            what="",
            photos=len(items),
            window=None,
            judged=False,
            prompt_version=SCAN_VERSION,
            app_version=__version__,
        )
    # The sequence reading decided this was an occasion; the day's own lines only name it.
    if not verdict.title:
        return None
    # The model read the day's own timestamps and what the lines said was in
    # the frames; event_window only knows where the pictures were. Either way
    # the window has to hold the day before it is written down.
    window = window_that_holds_the_day(verdict.window or event_window(items), items)
    return DiscoveredDay(
        day=day,
        title=verdict.title,
        subtitle=verdict.subtitle,
        what=verdict.what or what,
        photos=len(items),
        window=window,
        active_hours=active_hours(items),
        run_start=started,
        run_end=ended,
        window_photos=pictures_inside(window, items),
        prompt_version=SCAN_VERSION,
        app_version=__version__,
    )


def same_day_in(day: date, year: int) -> date:
    """The same calendar day in another year; 29 February falls back to the 28th."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def anniversaries_due(
    catalogue: Iterable[DiscoveredDay],
    on: date,
    *,
    window_days: int = 3,
) -> list[tuple[DiscoveredDay, int]]:
    """Discovered days whose anniversary falls near a date, roundest first.

    Ten years reads louder than nine, which is the whole appeal of arriving
    unannounced.

    The candidate is looked for in the years either side of the check as well
    as its own. A day at the end of December has its anniversary a few days
    before a check in early January, and trying only the check's own year put
    that candidate 364 days away — while counting the years to the calendar
    year rather than to the anniversary itself, which read eleven years for a
    tenth.
    """
    due: list[tuple[DiscoveredDay, int]] = []
    for entry in catalogue:
        for year in (on.year - 1, on.year, on.year + 1):
            years = year - entry.day.year
            if years < 1:
                continue
            if abs((same_day_in(entry.day, year) - on).days) <= window_days:
                due.append((entry, years))
                break
    return sorted(
        due, key=lambda pair: (0 if pair[1] % 10 == 0 else 1 if pair[1] % 5 == 0 else 2, -pair[1])
    )
