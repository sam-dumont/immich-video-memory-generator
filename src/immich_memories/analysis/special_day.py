"""Which days had something happen on them.

A recap should lead with the day the thing happened — a wedding, a birth, a
track day — and the pipeline had no way to tell one from an afternoon spent
photographing one subject. Volume alone does not say: the busiest day in a
real library is 166 photos of a work shoot inside a single hour, and the
second busiest is 413 of one street performer.

What separates them, measured on labelled days, is how long the day stayed
alive:

    a birth            289 photos   18 active hours   +
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
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.trip_detection import haversine_km

if TYPE_CHECKING:
    from collections.abc import Container, Iterable
    from datetime import date, datetime

    from immich_memories.config_models_llm import LLMConfig

from immich_memories.analysis.llm_failures import stop_if_this_is_our_bug
from immich_memories.analysis.special_day_title import (
    honest_title,
    line_the_day_can_keep,
    retitle_prompt,
    title_the_day_can_keep,
)

logger = logging.getLogger(__name__)

# A day has to stay alive this long, and hold this much, to be worth asking
# about. Both sit below every labelled positive and above every negative.
MIN_ACTIVE_HOURS = 6
MIN_PHOTOS = 20

_PROMPT = """One day from someone's photo library, sampled across the day, with the
pictures that go with these lines.

{lines}

What was this day? Take your time with it: the hours it ran, where it was,
who was there, what the pictures show. Coordinates are worth reading — a small
place name is often the edge of somewhere better known.

Then say whether something happened worth remembering years later, or whether
it was an ordinary day.

Every specific — a place, a distance, a count — comes from the lines above:
write what they show, as concretely as they show it.

Give it a title and a line under it, the way a photographer would caption a
set they were proud of. Not the date and not the place — those are already on
the card. Say something about the day.

If one clear event fills part of the day, give the clock times it ran between,
and leave the window null when the day was all one thing.

Answer with STRICT JSON only, no prose:
{{"special": true|false,
  "title": "<a few words, or empty>",
  "subtitle": "<one line, or empty>",
  "what": "<a few words, or empty>",
  "window": ["HH:MM", "HH:MM"] or null}}"""


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
        and active_hours(items) >= MIN_ACTIVE_HOURS
    }


def active_hours(items: Iterable) -> int:
    """How many hours of the clock a run put pictures in.

    Hours of the clock rather than hours elapsed: the six-hour bar and every
    number in this module's docstring were measured this way, and counting
    elapsed hours instead would quietly move the bar on the long runs.
    """
    return len({a.file_created_at.hour for a in items})


def run_extent(items: Iterable) -> tuple[datetime, datetime] | None:
    """When a run's first and last pictures were taken.

    Not the calendar day's bounds: the run is the occasion, and one labelled
    day was a birth that ran 45 continuous hours, from one evening to the
    afternoon two dates later. Anything that scopes itself to the date the
    run began stops at midnight, part-way through what happened.
    """
    times = [a.file_created_at for a in items]
    return (min(times), max(times)) if times else None


# A day ends when the photographs stop for this long, not at midnight. One
# labelled day was a birth that ran past midnight: the run began the evening
# before and ended the following afternoon as one continuous 45-hour run, and
# grouping by calendar date cut it into three, leaving the detector looking at
# the middle slice.
_NIGHT_GAP_HOURS = 5


def _runs_of_activity(assets: Iterable) -> dict[date, list]:
    """Group assets into runs separated by a long quiet gap.

    A wedding that goes past midnight, New Year, a birth that starts with
    contractions at ten in the evening — all of them are one occasion, and the
    calendar disagrees. Sleep is the honest boundary.

    Two runs can still begin on the same date — a morning of preparation, a
    long quiet afternoon, then the evening — and both belong to that date.
    Assigning rather than accumulating dropped the earlier one entirely, so a
    day could fall under a bar its two halves clear together.
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
            runs.setdefault(current[0].file_created_at.date(), []).extend(current)
            current = []
        current.append(asset)
    if current:
        runs.setdefault(current[0].file_created_at.date(), []).extend(current)
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


# Two places count as one if they are closer than this.
_SAME_PLACE_KM = 2.0
# A dominant place must hold this much of the day to define its window.
_DOMINANT_SHARE = 0.6
# A window has to be long enough to hold a memory. Without a floor, a dense
# burst in one place produced a 69-second window on a 12.6-hour day, and
# everything that day was about sat outside it.
_MIN_WINDOW = timedelta(minutes=30)
# And trimming has to actually remove something — this much clock time, and
# this much of the day. A ratio on its own read a race photographed from
# arrival to podium as "the day simply happened there" and returned nothing.
_MIN_TRIM = timedelta(minutes=45)
_MIN_TRIM_SHARE = 0.15


def event_window(assets: list) -> tuple[datetime, datetime] | None:
    """The part of a day the event actually occupies, or None for all of it.

    Some days are an event; some days contain one. A track day put 92% of its
    photos in one place inside 2.3 hours of a 10.6-hour day — the rest is a cat
    on a balcony that morning, and the memory should start at the circuit. A
    wedding also put 83% in one place, but across all fifteen hours it ran, so
    there is nothing to trim.

    What separates them is whether trimming to the dominant place would remove
    a meaningful part of the day. Asking instead how much of the day the place
    takes up punished the days photographed best: a race covered from arrival
    to podium filled two thirds of its day and was given no window at all.
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
            if haversine_km(lat, lon, *cluster["at"]) < _SAME_PLACE_KM:
                cluster["n"] += 1
                cluster["last"] = when
                break
        else:
            clusters.append({"at": (lat, lon), "n": 1, "first": when, "last": when})

    biggest = max(clusters, key=operator.itemgetter("n"))
    if biggest["n"] / len(located) < _DOMINANT_SHARE:
        return None

    day_span = located[-1][0] - located[0][0]
    window_span = biggest["last"] - biggest["first"]
    trimmed = day_span - window_span
    if window_span < _MIN_WINDOW:
        return None
    if trimmed < _MIN_TRIM or trimmed < _MIN_TRIM_SHARE * day_span:
        return None

    return biggest["first"], biggest["last"]


# How far outside its own pictures a written clock time may fall. The model
# reads the times off the evidence lines and rounds them — "20:00" for a last
# picture at 19:44 — and refusing that would throw away a good window.
_CLOCK_SLACK = timedelta(hours=1)


def _at_clock(written: Any, first: datetime, last: datetime) -> datetime | None:
    """A written HH:MM placed on the day's own timeline.

    Placed rather than parsed: a run of activity can cross midnight, so an
    02:00 end belongs to the following calendar date.
    """
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(written))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        return None
    moment = first.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    if moment < first - _CLOCK_SLACK:
        moment += timedelta(days=1)
    return moment if first - _CLOCK_SLACK <= moment <= last + _CLOCK_SLACK else None


def _window_the_model_gave(answer: dict, assets: list) -> tuple[datetime, datetime] | None:
    """The clock times the model put on the day's one clear event.

    Worth asking for because geometry cannot answer it. A race day's
    coordinates are identical from the moment the car is parked to the moment
    it leaves, so the cluster starts at arrival; the per-picture lines carry
    timestamps and say what is in the frame, so the model can tell arrival
    from the start of the thing that happened.
    """
    written = answer.get("window")
    if not isinstance(written, list) or len(written) != 2:
        return None
    times = [a.file_created_at for a in assets]
    first, last = min(times), max(times)
    start, end = _at_clock(written[0], first, last), _at_clock(written[1], first, last)
    if start is None or end is None or end - start < _MIN_WINDOW:
        return None
    return start, end


def _line_for(asset: Any, described: str | None) -> str:
    """One asset's line: when it was taken, where, who was in it, what it shows."""
    exif = getattr(asset, "exif_info", None)
    where = ", ".join(p for p in (getattr(exif, "city", None), getattr(exif, "country", None)) if p)
    people = [p.name for p in (getattr(asset, "people", None) or []) if getattr(p, "name", "")]
    bits = [asset.file_created_at.strftime("%H:%M")]
    if where:
        bits.append(where)
    # Coordinates as well as the place name: a model that knows the area
    # can tell a racing circuit from the village it is named after, and
    # a coordinate pair is a fact the pictures cannot contradict.
    lat = getattr(exif, "latitude", None) if exif else None
    lon = getattr(exif, "longitude", None) if exif else None
    if lat and lon:
        bits.append(f"{lat:.4f},{lon:.4f}")
    if people:
        bits.append(f"{len(people)} recognised: {', '.join(people[:3])}")
    if described:
        bits.append(str(described)[:160])
    return "  " + "  ".join(bits)


def _describe(assets: list, seen: list[str] | None = None) -> str:
    """The day as text: one line per sampled picture, in the order they were taken.

    `seen` is what a look reported, one line per asset in the same order. It
    takes precedence over any description already on the asset, and it is
    passed rather than written onto the assets because these are the caller's
    objects, not ours.

    WHAT is in the frame matters as much as when and where. Without it the
    model can place a day and name who was there but not say what happened: a
    track day came back as "Driving through a place" because nothing had
    mentioned the cars.
    """
    # The date itself, once, at the top. Given only clock times the model
    # filled the gap: a February day came back subtitled "July 2, 2024".
    lines = [assets[0].file_created_at.strftime("  date: %A %d %B %Y")] if assets else []
    for index, asset in enumerate(assets):
        looked = seen[index] if seen and index < len(seen) else None
        lines.append(_line_for(asset, looked or getattr(asset, "llm_description", None)))
    return "\n".join(lines)


def _ask(
    prompt: str,
    llm_config: LLMConfig,
    timeout_seconds: int,
    images: list[bytes],
    thinking: bool = False,
) -> str:
    """One question to the configured provider, with the day's pictures if any.

    Routing belongs to llm_query and nowhere else: the vision call used to
    POST OpenAI-style whatever the provider was, so every Ollama server it
    met answered 404 and the day came back ordinary.
    """
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(
            prompt,
            llm_config,
            temperature=0.1,
            timeout_seconds=timeout_seconds,
            images=images,
            thinking=thinking,
        )
    )


_LOOK_PROMPT = "One line per picture, in order, numbered: what is in it."

# Reasoning about a judgement call costs 5-10x the latency of a fast answer, and
# the scan asks only a handful of days a year.
_THINKING_TIMEOUT_SECONDS = 300


def _numbered_lines(raw: str) -> list[str]:
    """The model's lines in order, stripped of whatever numbering it chose."""
    lines = [re.sub(r"^\W*\d+[.):]?\s*", "", line).strip() for line in raw.splitlines()]
    return [line for line in lines if line]


def _look_at(
    thumbnails: list[tuple[Any, bytes]],
    llm_config: LLMConfig,
    timeout_seconds: int,
) -> list[str]:
    """Step one: fast eyes. What is in each picture, in the order they came.

    Deliberately the smallest prompt in the file. Every rule added here made a
    small model worse, and the judgement that follows is where the thinking is
    meant to happen.
    """
    try:
        raw = _ask(_LOOK_PROMPT, llm_config, timeout_seconds, [image for _, image in thumbnails])
    except Exception as exc:  # noqa: BLE001 - a look that fails is not a verdict
        stop_if_this_is_our_bug(exc, "special-day look")
        logger.debug("Special-day look failed: %s", type(exc).__name__)
        return []
    # A null content is documented mlx-vlm behaviour, and the rest of this
    # module guards it explicitly rather than coercing it away.
    if not raw:
        logger.debug("Special-day look came back empty")
        return []
    seen = _numbered_lines(raw)
    # A day that comes back ordinary is diagnosed from here, so the lines the
    # judgement actually read have to be recoverable after the fact.
    for index, line in enumerate(seen, start=1):
        logger.debug("Special-day look %d: %s", index, line)
    return seen


def _asked_again(
    rejected: str,
    assets: list,
    lines: str,
    llm_config: LLMConfig,
    timeout_seconds: int,
    images: list[bytes],
    thinking: bool,
) -> str:
    """One more attempt at a title, once the guard has taken the first one away.

    Once, never twice: a model that has now been told what the evidence shows
    and answered with an invention anyway is not going to be talked round on a
    third try, and every attempt is a live call on a scan that makes a handful
    per year. Same call shape as the judgement it follows, so it inherits the
    same routing, cache and thinking budget.
    """
    try:
        raw = _ask(
            retitle_prompt(lines, rejected=rejected, assets=assets),
            llm_config,
            timeout_seconds,
            images,
            thinking=thinking,
        )
    except Exception as exc:  # noqa: BLE001 - a second ask that fails is not a verdict
        stop_if_this_is_our_bug(exc, "special-day retitle")
        logger.debug("Special-day retitle failed: %s", type(exc).__name__)
        return ""
    # A null content is documented mlx-vlm behaviour, guarded here exactly as
    # the judgement above guards it rather than coerced away.
    if not raw:
        logger.debug("Special-day retitle came back empty")
        return ""
    answer = _json_in(raw)
    if answer is None:
        return ""
    return title_the_day_can_keep(str(answer.get("title", ""))[:60].strip(), assets, evidence=lines)


def _json_in(raw: str) -> dict | None:
    """The one JSON object in an answer, or nothing if there is none to read."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass(frozen=True)
class SpecialDay:
    """What the model made of a day."""

    special: bool
    title: str = ""
    subtitle: str = ""
    what: str = ""
    window: tuple[datetime, datetime] | None = None


def ask_if_special(
    assets: list,
    llm_config: LLMConfig,
    *,
    timeout_seconds: int = 30,
    thumbnails: list[tuple[Any, bytes]] | None = None,
) -> SpecialDay:
    """Ask the model whether a day looks like an occasion, and name it.

    With thumbnails the model sees the day; without them it reasons from times,
    places and recognised names alone. The difference is the difference between
    "Driving through a place" and knowing what was being driven.

    Each thumbnail arrives paired with the asset it was drawn from, and the
    lines are written from exactly those assets. The prompt tells the model
    the lines and the pictures go together; sampling twice made that untrue,
    and it read one picture's time, place and names against another's.
    """
    if not assets:
        return SpecialDay(special=False)

    sampled = [asset for asset, _ in thumbnails] if thumbnails else sample_across_day(assets)
    images = [image for _, image in thumbnails or []]
    # Two calls, never one. query_llm refuses thinking alongside images because
    # multi-image reasoning is a measured runaway, so the shape is enforced by
    # the API rather than by remembering: a single call carrying both cannot be
    # written by accident. Measured on 14 days, the one-call version reasoned
    # into its own answer and truncated 6 of them past parsing.
    reasons = bool(getattr(llm_config, "thinking", False)) and bool(images)
    seen = _look_at(thumbnails or [], llm_config, timeout_seconds) if reasons else None
    if reasons:
        images = []
        timeout_seconds = max(timeout_seconds, _THINKING_TIMEOUT_SECONDS)
    lines = _describe(sampled, seen)
    prompt = _PROMPT.format(lines=lines)
    try:
        raw = _ask(prompt, llm_config, timeout_seconds, images, thinking=reasons)
    except Exception as exc:  # noqa: BLE001 - an unreachable model is not a verdict
        stop_if_this_is_our_bug(exc, "special-day question")
        logger.debug("Special-day question failed: %s", type(exc).__name__)
        return SpecialDay(special=False)

    # A null content is documented mlx-vlm behaviour, which is why llm_query
    # retries. Silence is not a verdict either, and reading it as one ended a
    # multi-hour scan on a TypeError.
    if not raw:
        logger.debug("Special-day question came back empty")
        return SpecialDay(special=False)

    answer = _json_in(raw)
    if answer is None:
        return SpecialDay(special=False)
    special = bool(answer.get("special"))
    written = str(answer.get("title", ""))[:60].strip()
    what = str(answer.get("what", ""))[:80].strip()
    title = title_the_day_can_keep(written, assets, evidence=lines)
    # Only for a day that is going to be kept. An ordinary day is discarded
    # whatever it is called, and a second live call to name it better is spent
    # on nothing.
    if special and written and not title:
        title = _asked_again(written, assets, lines, llm_config, timeout_seconds, images, reasons)
    return SpecialDay(
        special=special,
        title=title or honest_title(assets, what=what, evidence=lines),
        subtitle=line_the_day_can_keep(
            str(answer.get("subtitle", ""))[:90].strip(), assets, evidence=lines
        ),
        what=what,
        window=_window_the_model_gave(answer, assets),
    )
