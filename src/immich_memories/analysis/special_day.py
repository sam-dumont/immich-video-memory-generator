"""Which days had something happen on them.

A recap should lead with the day the thing happened — a wedding, a birth, a
track day — and the pipeline had no way to tell one from an afternoon spent
photographing one subject. Volume alone does not say: the busiest day in a
real library is 166 photos of a work shoot inside a single hour, and the
second busiest is 413 of one street performer.

What separates them, measured on labelled days, is how long the day stayed
alive:

    birth of a son     289 photos   18 active hours   +
    wedding party       48 photos   12 active hours   +
    track day          133 photos    7 active hours   +
    apartment viewing  258 photos    5 active hours   -
    street performer   413 photos    3 active hours   -
    work shoot         166 photos    1 active hour    -

No overlap. But the rule is loose on its own — 22% of days in that library
clear six hours — so it is a filter, not a verdict. What passes goes to the
model, which is asked the question a person would ask: does this look like a
day something happened?
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import operator
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Container, Iterable
    from datetime import date, datetime

    from immich_memories.config_models import LLMConfig

logger = logging.getLogger(__name__)

# A day has to stay alive this long, and hold this much, to be worth asking
# about. Both sit below every labelled positive and above every negative.
MIN_ACTIVE_HOURS = 6
MIN_PHOTOS = 20

# Capitalised words a title may use without naming anything: sentence starts
# and the calendar. Anything else has to come from the day itself.
_EVERYDAY_CAPITALS = {
    "a",
    "an",
    "the",
    "and",
    "with",
    "at",
    "in",
    "on",
    "of",
    "to",
    "from",
    "for",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "morning",
    "afternoon",
    "evening",
    "night",
    "day",
    "birthday",
    "wedding",
    "christmas",
    "easter",
    "new",
    "year",
    "eve",
}

_PROMPT = """One day from someone's photo library, sampled across the day, with the
pictures that go with these lines.

{lines}

What was this day? Take your time with it: the hours it ran, where it was,
who was there, what the pictures show. Coordinates are worth reading — a small
place name is often the edge of somewhere better known.

Then say whether something happened worth remembering years later, or whether
it was an ordinary day.

Where you cannot tell, stay vague. "A track day" is right and "a day at Spa"
is wrong, and vague costs nothing.

Give it a title and a line under it, the way a photographer would caption a
set they were proud of. Not the date and not the place — those are already on
the card. Say something about the day.

Answer with STRICT JSON only, no prose:
{{"special": true|false,
  "title": "<a few words, or empty>",
  "subtitle": "<one line, or empty>",
  "what": "<a few words, or empty>"}}"""


def candidate_days(
    assets: Iterable,
    *,
    away_days: Container[date] = frozenset(),
) -> dict[date, list]:
    """Days worth asking the model about, by the cheap structural test.

    Keeps the model off the other 78% of days, which is what makes asking
    affordable at all.

    away_days are excluded: a holiday is full of days that clear every bar
    here, and a trip memory already tells that story end to end. What is left
    is the day that stands out from an ordinary run of them.
    """
    return {
        day: items
        for day, items in _runs_of_activity(assets).items()
        if day not in away_days
        and len(items) >= MIN_PHOTOS
        and len({a.file_created_at.hour for a in items}) >= MIN_ACTIVE_HOURS
    }


# A day ends when the photographs stop for this long, not at midnight. the child
# was born at 23:57: the night ran from 22:13 the evening before to the
# following afternoon as one continuous 45-hour run, and grouping by calendar
# date cut it into three, leaving the detector looking at the middle slice.
_NIGHT_GAP_HOURS = 5


def _runs_of_activity(assets: Iterable) -> dict[date, list]:
    """Group assets into runs separated by a long quiet gap.

    A wedding that goes past midnight, New Year, a birth that starts with
    contractions at ten in the evening — all of them are one occasion, and the
    calendar disagrees. Sleep is the honest boundary.
    """
    dated = sorted(
        (a for a in assets if getattr(a, "file_created_at", None) is not None),
        key=lambda a: a.file_created_at,
    )
    runs: dict[date, list] = {}
    current: list = []
    for asset in dated:
        if (
            current
            and (asset.file_created_at - current[-1].file_created_at).total_seconds()
            > _NIGHT_GAP_HOURS * 3600
        ):
            runs[current[0].file_created_at.date()] = current
            current = []
        current.append(asset)
    if current:
        runs[current[0].file_created_at.date()] = current
    return runs


def days_covered_by_trips(trips: Iterable) -> set[date]:
    """Every date inside a detected trip, for keeping them out of the above."""
    covered: set[date] = set()
    for trip in trips:
        span = (trip.end_date - trip.start_date).days
        covered.update(trip.start_date + timedelta(days=n) for n in range(span + 1))
    return covered


def sample_across_day(assets: list, count: int = 8) -> list:
    """Spread the sample over the day's hours, not its busiest minutes.

    Taking the first N would describe one burst, which is the very thing the
    question is meant to see past.
    """
    by_hour: dict[int, list] = collections.defaultdict(list)
    for asset in assets:
        by_hour[asset.file_created_at.hour].append(asset)

    picked: list = []
    hours = sorted(by_hour)
    while hours and len(picked) < count:
        for hour in hours.copy():
            if len(picked) >= count:
                break
            bucket = by_hour[hour]
            if bucket:
                picked.append(bucket.pop(len(bucket) // 2))
            if not bucket:
                hours.remove(hour)
    return sorted(picked, key=lambda a: a.file_created_at)


def _titled(text: str, people: set[str]) -> str:
    return text if _is_a_real_title(text, people) else ""


def _place_vocabulary(assets: list) -> set[str]:
    """Every place name the day recorded, or empty if it recorded none."""
    words: set[str] = set()
    for asset in assets:
        exif = getattr(asset, "exif_info", None)
        for attr in ("city", "state", "country"):
            value = getattr(exif, attr, None) if exif else None
            if value:
                words.update(re.findall(r"[\w\u00c0-\u024f]+", str(value)))
    return {w.casefold() for w in words}


def _grounding_vocabulary(assets: list) -> set[str]:
    """Every proper noun the model was actually given."""
    words: set[str] = set()
    for asset in assets:
        exif = getattr(asset, "exif_info", None)
        for attr in ("city", "state", "country"):
            value = getattr(exif, attr, None) if exif else None
            if value:
                words.update(re.findall(r"[\w\u00c0-\u024f]+", str(value)))
        for person in getattr(asset, "people", None) or []:
            if getattr(person, "name", ""):
                words.update(re.findall(r"[\w\u00c0-\u024f]+", person.name))
    return {w.casefold() for w in words}


def _knows_where_it_was(assets: list) -> bool:
    """Did anything tell us where this day happened?"""
    for asset in assets:
        exif = getattr(asset, "exif_info", None)
        if not exif:
            continue
        if getattr(exif, "city", None) or getattr(exif, "country", None):
            return True
        if getattr(exif, "latitude", None) and getattr(exif, "longitude", None):
            return True
    return False


def _is_a_real_title(text: str, people: set[str]) -> bool:
    """Reject the two things the model falls back on when it is unsure.

    Asked to prefer the plain true thing, it starts answering with the one
    fact it is certain of: "Monday 13 August 2007", or simply a
    person's name. Both are already on the card. Seven of twenty-three titles in a
    library sweep came back like that.
    """
    if not text:
        return False
    stripped = re.sub(
        r"[\d,]|\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", "", text, flags=re.IGNORECASE
    )
    stripped = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|october"
        r"|november|december|at|in|on|the|of)\b",
        "",
        stripped,
        flags=re.IGNORECASE,
    )
    if not stripped.strip():
        return False
    return text.casefold() not in people


# How close a written place has to be to one the day recorded. EXIF is in the
# local language and the model writes English: a local spelling and its English form are the
# same place and must pass; two different towns are not and must not.
_SAME_PLACE_RATIO = 0.75
_PLACE_PREPOSITIONS = ("in", "at", "near", "around", "from", "to", "de", "outside")


def _named_places(text: str) -> list[str]:
    """Capitalised words used as somewhere, rather than as something."""
    pattern = (
        r"\b(?:" + "|".join(_PLACE_PREPOSITIONS) + r")\s+"
        r"((?:[A-Z\u00c0-\u00dd][\w\u00c0-\u024f'-]+(?:[ -](?:de|la|le|sur|of|the))?\s*){1,3})"
    )
    return [m.strip() for m in re.findall(pattern, text)]


def _place_is_real(place: str, vocabulary: set[str]) -> bool:
    """Did the day actually happen anywhere by this name?"""
    import difflib

    for word in re.findall(r"[\w\u00c0-\u024f]+", place):
        if len(word) < 4:
            continue
        if difflib.get_close_matches(
            word.casefold(), list(vocabulary), n=1, cutoff=_SAME_PLACE_RATIO
        ):
            return True
    return False


def _only_if_grounded(text: str, vocabulary: set[str], *, located: bool, places: set[str]) -> str:
    """Drop a line that names a place the day cannot support.

    Asked about a night out with no city and no GPS anywhere in it, the model
    answered with three names and a town. The names were real
    and a place was invented, in spite of the prompt forbidding it. A title card
    is the wrong place for a plausible invention.

    The check only applies when the day carries no location at all. Given
    coordinates, naming the circuit they sit on is inference from data, and an
    earlier version that policed every capitalised word threw away "Audi R8
    V10 Track Day at a place" — a correct reading of the pictures — because
    no EXIF field happens to contain the word Audi.
    """
    # A guessed brand is CamelCase and the day never mentions it. Told twice
    # in the prompt not to name an event it could not read, the model answered
    # "Attending KubeCon in a place" and then, on the same day, "Attending
    # GitLab All-Hands" — recognising a hall full of lanyards and inventing
    # which conference it was. This catches that shape without touching Audi,
    # R8 or a place, none of which carry an internal capital.
    for coined in re.findall(r"\b[A-Z][a-z]+[A-Z][\w]*", text):
        if coined.casefold() not in vocabulary:
            logger.info("Dropping %r: %r looks like a guessed name", text, coined)
            return ""

    # A place the day never recorded. Asked about a track day at a place — with
    # three villages all in its EXIF — the model
    # answered "the famous circuit", Belgium's famous circuit rather
    # than the one the coordinates sit on. Recognising the kind of place and
    # naming the well-known instance of it is the failure that keeps recurring.
    # Only when the day recorded place names at all. With coordinates and no
    # city, naming the circuit they sit on is inference this cannot check, and
    # rejecting it would throw away the model's best work.
    if places:
        for place in _named_places(text):
            if not _place_is_real(place, places):
                logger.info("Dropping %r: the day was never in %r", text, place)
                return ""

    if not text or located:
        return text
    for word in re.findall(r"\b[A-Z\u00c0-\u00dd][\w\u00c0-\u024f'-]{2,}", text):
        if word.casefold() not in vocabulary and word.casefold() not in _EVERYDAY_CAPITALS:
            logger.info(
                "Dropping title %r: the day has no location and nothing mentions %r", text, word
            )
            return ""
    return text


# Two places count as one if they are closer than this.
_SAME_PLACE_KM = 2.0
# A dominant place must hold this much of the day to define its window...
_DOMINANT_SHARE = 0.6
# ...and occupy less than this much of it, or the day simply happened there.
_WINDOW_SHARE_OF_DAY = 0.5


def _km_apart(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = radians(a[0]), radians(a[1]), radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


def event_window(assets: list) -> tuple[datetime, datetime] | None:
    """The part of a day the event actually occupies, or None for all of it.

    Some days are an event; some days contain one. A track day put 92% of its
    photos in one place inside 2.3 hours of a 10.6-hour day — the rest is a cat
    on a balcony that morning, and the memory should start at the circuit. A
    wedding also put 83% in one place, but across all fifteen hours it ran, so
    there is nothing to trim.

    What separates them is not whether a place dominates — all of them do — but
    how much of the day it takes up.
    """
    located = [
        (a.file_created_at, a.exif_info.latitude, a.exif_info.longitude)
        for a in assets
        if getattr(a, "exif_info", None)
        and getattr(a.exif_info, "latitude", None)
        and getattr(a.exif_info, "longitude", None)
    ]
    if len(located) < 4:
        return None

    located.sort()
    clusters: list[dict] = []
    for when, lat, lon in located:
        for cluster in clusters:
            if _km_apart((lat, lon), cluster["at"]) < _SAME_PLACE_KM:
                cluster["n"] += 1
                cluster["last"] = when
                break
        else:
            clusters.append({"at": (lat, lon), "n": 1, "first": when, "last": when})

    biggest = max(clusters, key=operator.itemgetter("n"))
    if biggest["n"] / len(located) < _DOMINANT_SHARE:
        return None

    day_span = (located[-1][0] - located[0][0]).total_seconds()
    window_span = (biggest["last"] - biggest["first"]).total_seconds()
    if day_span <= 0 or window_span / day_span >= _WINDOW_SHARE_OF_DAY:
        return None

    return biggest["first"], biggest["last"]


def _describe(assets: list) -> str:
    # The date itself, once, at the top. Given only clock times the model
    # filled the gap: a February day came back subtitled "July 2, 2024".
    lines = [assets[0].file_created_at.strftime("  date: %A %d %B %Y")] if assets else []
    for asset in assets:
        exif = getattr(asset, "exif_info", None)
        where = ", ".join(
            p for p in (getattr(exif, "city", None), getattr(exif, "country", None)) if p
        )
        people = [p.name for p in (getattr(asset, "people", None) or []) if getattr(p, "name", "")]
        bits = [asset.file_created_at.strftime("%H:%M")]
        if where:
            bits.append(where)
        # Coordinates as well as the place name: a model that knows the area
        # can tell a racing circuit from the village it is named after, and
        # "50.6358, 4.3630" is a fact the pictures cannot contradict.
        lat = getattr(exif, "latitude", None) if exif else None
        lon = getattr(exif, "longitude", None) if exif else None
        if lat and lon:
            bits.append(f"{lat:.4f},{lon:.4f}")
        if people:
            bits.append(f"{len(people)} recognised: {', '.join(people[:3])}")
        # WHAT is in the frame, when anything has looked. Without it the model
        # can place a day and name who was there but not say what happened:
        # a track day came back as "Driving through a place" because nothing
        # had mentioned the cars.
        described = getattr(asset, "llm_description", None)
        if described:
            bits.append(str(described)[:160])
        lines.append("  " + "  ".join(bits))
    return "\n".join(lines)


def _ask_text_only(prompt: str, llm_config: LLMConfig, timeout_seconds: int) -> str:
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(prompt, llm_config, temperature=0.1, timeout_seconds=timeout_seconds)
    )


def _ask_with_images(
    prompt: str,
    thumbnails: list[bytes],
    llm_config: LLMConfig,
    timeout_seconds: int,
) -> str:
    """One call carrying the day's sample as images."""
    import base64

    import httpx

    from immich_memories.analysis.llm_query import build_llm_timeout

    content: list[dict] = [{"type": "text", "text": prompt}]
    content += [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(data).decode("utf-8"),
                "detail": "low",
            },
        }
        for data in thumbnails
    ]
    headers = {"Authorization": f"Bearer {llm_config.api_key}"} if llm_config.api_key else {}
    resp = httpx.post(
        f"{llm_config.base_url}/chat/completions",
        json={
            "model": llm_config.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 300,
        },
        headers=headers,
        timeout=build_llm_timeout(float(timeout_seconds)),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


@dataclass(frozen=True)
class SpecialDay:
    """What the model made of a day."""

    special: bool
    title: str = ""
    subtitle: str = ""
    what: str = ""


def ask_if_special(
    assets: list,
    llm_config: LLMConfig,
    *,
    timeout_seconds: int = 30,
    thumbnails: list[bytes] | None = None,
) -> SpecialDay:
    """Ask the model whether a day looks like an occasion, and name it.

    With thumbnails the model sees the day; without them it reasons from times,
    places and recognised names alone. The difference is the difference between
    "Driving through a place" and knowing what was being driven.
    """
    if not assets:
        return SpecialDay(special=False)

    prompt = _PROMPT.format(lines=_describe(sample_across_day(assets)))
    try:
        raw = (
            _ask_with_images(prompt, thumbnails, llm_config, timeout_seconds)
            if thumbnails
            else _ask_text_only(prompt, llm_config, timeout_seconds)
        )
    except Exception as exc:  # noqa: BLE001 - an unreachable model is not a verdict
        logger.debug("Special-day question failed: %s", type(exc).__name__)
        return SpecialDay(special=False)

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return SpecialDay(special=False)
    try:
        answer = json.loads(match.group(0))
    except json.JSONDecodeError:
        return SpecialDay(special=False)
    grounded = _grounding_vocabulary(assets)
    located = _knows_where_it_was(assets)
    places = _place_vocabulary(assets)
    named = {
        p.name.casefold()
        for a in assets
        for p in (getattr(a, "people", None) or [])
        if getattr(p, "name", "")
    }
    return SpecialDay(
        special=bool(answer.get("special")),
        title=_only_if_grounded(
            _titled(str(answer.get("title", ""))[:60].strip(), named),
            grounded,
            located=located,
            places=places,
        ),
        subtitle=_only_if_grounded(
            str(answer.get("subtitle", ""))[:90].strip(),
            grounded,
            located=located,
            places=places,
        ),
        what=str(answer.get("what", ""))[:80].strip(),
    )
