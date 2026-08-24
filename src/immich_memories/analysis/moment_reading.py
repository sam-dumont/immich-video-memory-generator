"""What a moment was, and which of its frames are worth the expensive look.

Curation runs moment-first: read what a moment is from all of its photographs
at once, take the frames worth keeping, analyse only those, and judge the
memory as a whole at the end. This module is the cheap wide pass at the front.

Photographs are tiled into numbered contact sheets and read a sheet at a time.
One composite image is the whole trick: sending many images in one call runs
away and truncates, while a grid is a single image and does not. Measured on a
real day, 215 photographs read in 14 calls and 22 seconds, against roughly
1.5s per photograph to describe them one by one.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from PIL.Image import Image

    from immich_memories.config_models_llm import LLMConfig

# A moment goes on ONE sheet. Split across several, each sheet answers about
# its own third of an event and the first answer wins by accident: measured on
# a 37-photograph episode, sheet one said "cyclists and cars at a race track"
# while later sheets named the circuit and the car.
#
# Past this many photographs a single sheet is postage stamps, so a very large
# moment is split rather than shrunk to nothing.
MAX_SHEET_TILES = 120

# How wide a sheet may be before the encoder downscales it and tiles lose the
# detail the reading depends on.
MAX_SHEET_PX = 2100

# Sheets are laid out WIDE, never tall. The same 37 photographs at 1920x2240
# read worse than at 2080x1300 with SMALLER tiles — the tall sheet described
# less and its shortlist collapsed to "keep all 37". A tall image is
# downscaled harder before the model ever sees it, so shape matters more than
# how big each tile is.
_TARGET_ASPECT = 1.6
_MAX_TILE = 400
_MIN_TILE = 120
_SHEET_BACKGROUND = (18, 18, 18)

T = TypeVar("T")


@dataclass(frozen=True)
class SheetReading:
    """One sheet's answer: what it was, and which tiles to keep."""

    about: str
    subjects: tuple[str, ...]
    keep: tuple[int, ...]


def sheet_layout(count: int) -> tuple[int, int]:
    """Columns and tile size for one wide sheet of `count` photographs."""
    columns = max(1, round(math.sqrt(count * _TARGET_ASPECT)))
    tile = min(_MAX_TILE, MAX_SHEET_PX // columns)
    rows = -(-count // columns)
    if rows * tile > MAX_SHEET_PX * 1.2:
        tile = int(MAX_SHEET_PX * 1.2) // rows
    return columns, max(_MIN_TILE, tile)


def sheets_of(items: list[T], per_sheet: int = MAX_SHEET_TILES) -> list[list[tuple[int, T]]]:
    """Cut a moment into sheets, numbering tiles across the whole moment.

    The model answers in tile numbers, so numbering has to run across the
    moment rather than restart on each sheet — otherwise sheet three's "4"
    and sheet one's "4" are the same answer about different photographs.
    """
    return [
        [(offset + n + 1, item) for n, item in enumerate(items[offset : offset + per_sheet])]
        for offset in range(0, len(items), per_sheet)
    ]


def _objects_in(raw: str) -> list[str]:
    """Every balanced {...} in the text, outermost first.

    Answers arrive fenced, prefaced, or with the prompt's own template echoed
    back, so the first brace is not reliably the answer.
    """
    found: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                found.append(raw[start : index + 1])
    return found


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _numbers(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool))


def read_sheet_verdict(raw: str | None) -> SheetReading | None:
    """What the model said about one sheet, or nothing if it did not say.

    A sheet that truncates mid-answer must read as no answer rather than as a
    moment about nothing: the densest sheet of the densest day is exactly the
    one whose answer runs longest, so silent acceptance loses the moment that
    mattered most.
    """
    if not raw:
        return None
    for candidate in _objects_in(raw):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        about = payload.get("about")
        if not isinstance(about, str) or not about.strip():
            continue
        return SheetReading(
            about=about.strip(),
            subjects=_texts(payload.get("subjects")),
            keep=_numbers(payload.get("best")),
        )
    return None


def tile_sheet(frames: list[tuple[int, Image | None]]) -> Image | None:
    """Lay numbered frames out as one image, or nothing if there are none.

    The model answers in tile numbers, so every tile carries its number burnt
    into the corner. A frame that could not be fetched leaves its number on an
    empty tile rather than shifting every frame after it — a 404 thumbnail
    would otherwise silently renumber the rest of the sheet and make the
    answer point at the wrong photographs.
    """
    from PIL import Image, ImageDraw

    if not frames:
        return None
    columns, tile = sheet_layout(len(frames))
    rows = -(-len(frames) // columns)
    sheet = Image.new("RGB", (columns * tile, rows * tile), _SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    label = max(16, tile // 11)
    for position, (number, frame) in enumerate(frames):
        left = (position % columns) * tile
        top = (position // columns) * tile
        if frame is not None:
            thumbnail = frame.copy()
            thumbnail.thumbnail((tile - 6, tile - 6))
            sheet.paste(thumbnail, (left + 3, top + 3))
        text = str(number)
        draw.rectangle(
            [left + 3, top + 3, left + 3 + label * (0.6 * len(text) + 0.8), top + 3 + label],
            fill=(0, 0, 0),
        )
        draw.text((left + 7, top + 5), text, fill=(255, 255, 255))
    return sheet


# Ages that change how a person should be described. A newborn read as one of
# the adults in the room until the age was supplied.
_NEWBORN_DAYS = 1
_DAYS_IN_A_MONTH = 30
_DAYS_IN_A_YEAR = 365
_MONTHS_UNTIL_YEARS = 730
_DAYS_UNTIL_MONTHS = 60


def _age_at(person: object, when: datetime | None) -> str | None:
    """How old someone was that day, in the terms a description would use."""
    born = getattr(person, "birth_date", None)
    if not isinstance(born, datetime) or when is None:
        return None
    days = (when.date() - born.date()).days
    if days < 0:
        return None
    if days <= _NEWBORN_DAYS:
        return "newborn"
    if days < _DAYS_UNTIL_MONTHS:
        return f"{days} days old"
    if days < _MONTHS_UNTIL_YEARS:
        return f"{days // _DAYS_IN_A_MONTH} months old"
    return f"aged {days // _DAYS_IN_A_YEAR}"


def who_was_there(numbered: list[tuple[int, object]]) -> str:
    """Who Immich has identified in each tile, and how old they were.

    Supplied rather than read: asked to take names off wristbands the model
    misread one. Given the tags it gets them right, and given the ages it
    stops calling a newborn a parent.

    Says so explicitly when nobody is identified — an empty block reads as no
    instruction at all, which is the case where it invents.
    """
    lines: list[str] = []
    for number, asset in numbered:
        present = []
        for person in getattr(asset, "people", None) or []:
            name = getattr(person, "name", None)
            if not name:
                continue
            age = _age_at(person, getattr(asset, "file_created_at", None))
            present.append(f"{name} ({age})" if age else name)
        if present:
            lines.append(f"  photo {number}: {', '.join(sorted(set(present)))}")
    if not lines:
        return "\n\nNobody in these photographs has been identified by name."
    return "\n\nWho is in them:\n" + "\n".join(lines)


# The reading is asked for in the terms the pipeline can check: what happened,
# what it turns out to be about, and which frames are worth the expensive look.
# Every rule here earned its place against a measured failure, so they are
# named rather than accumulated:
#   - a set of photographs of one person read as twins until told otherwise
#   - asked to sound like the person who was there, it invented a name and a sex
#   - subjects came back as inventories of the furniture and the weather
SHEET_PROMPT = """These numbered photographs were taken close together in time.

1. What was happening here? One plain sentence describing only what these
   photographs show.

   Read what is there, including words: a number on a bib, branding on a
   banner. Do NOT name the place — where this was is given below if it is
   known, and if it is not, say nothing about where it was.
   WHO is in each photograph is given below, and
   that list is the only source of names — never read a name off a wristband
   or document, and never invent one. Where no name is given, say "a baby",
   "a child", "two adults". Do not state a sex, an age or a relationship the
   photographs do not show. The same person photographed many times is still
   ONE person. If you are unsure what an event is, describe what is visible
   and stop.

2. What does this set turn out to be ABOUT — at most four things, and only
   what someone would still care about years later. Equipment, walls, weather
   and clothing are never subjects. If the same thing appears in many
   photographs it is still one thing.

3. Which of these numbered photographs are worth keeping, for someone
   remembering this day? Judge what each photograph SHOWS. A photograph is not
   better merely because a face is in it.

Respond as JSON only, and keep every string short:
{"about": "...", "subjects": ["..."], "best": [numbers], "why": "..."}"""

# Measured: the densest sheet of the densest day truncated at 900 and its
# reading was lost. Size past the answer, not to it.
SHEET_ANSWER_TOKENS = 3000

# The sheet is evidence, not a photograph: enough quality to read a race number
# off a tile, not so much that a moment costs a megabyte of base64.
_SHEET_QUALITY = 85
_SHEET_TEMPERATURE = 0.1


@dataclass(frozen=True)
class MomentReading:
    """What a moment was, and which of its photographs are worth the deep look.

    `keep` holds whatever was handed in. This pass is deliberately agnostic
    about what makes a set of photographs a set — selection hands it a moment,
    discovery hands it a day, boundary detection hands it a window — so it
    gives back the same objects it was given rather than a type of its own.
    """

    about: str
    subjects: tuple[str, ...]
    keep: tuple[Any, ...]


def _read_one_sheet(
    numbered: list[tuple[int, Any]],
    frames: dict[str, Image],
    llm_config: LLMConfig,
) -> SheetReading | None:
    """Ask about one sheet, or return nothing if it could not be read."""
    import asyncio
    import io

    from immich_memories.analysis.llm_query import query_llm

    sheet = tile_sheet([(n, frames.get(getattr(a, "id", ""))) for n, a in numbered])
    if sheet is None:
        return None
    buffer = io.BytesIO()
    sheet.save(buffer, "JPEG", quality=_SHEET_QUALITY)
    answer = asyncio.run(
        query_llm(
            prompt_for([asset for _n, asset in numbered]),
            llm_config,
            temperature=_SHEET_TEMPERATURE,
            max_tokens=SHEET_ANSWER_TOKENS,
            images=[buffer.getvalue()],
        )
    )
    return read_sheet_verdict(answer)


def read_moment(
    assets: list[Any],
    frames: dict[str, Image],
    llm_config: LLMConfig,
    keep_cap: int | None = None,
) -> MomentReading:
    """Read a whole moment from its sheets, and keep what they chose.

    One call per sheet rather than one per photograph: measured on a real day,
    215 photographs read in 14 calls and 22 seconds, against roughly 1.5s each
    to describe them individually. The reading is what the expensive per-photo
    look is then spent on.

    A moment no sheet could read keeps nothing. An unreadable answer is a
    failure to look, not a verdict that there was nothing here, and inventing
    a shortlist out of one would spend the deep look on frames chosen at
    random.
    """
    readings: list[SheetReading] = []
    kept: list[Any] = []
    for sheet in sheets_of(assets):
        reading = _read_one_sheet(sheet, frames, llm_config)
        if reading is None:
            continue
        readings.append(reading)
        # Only this sheet's own numbers. A model that answers with a number
        # it was not shown must not be handed a different sheet's photograph.
        on_this_sheet = dict(sheet)
        kept.extend(on_this_sheet[n] for n in reading.keep if n in on_this_sheet)
    if not readings:
        return MomentReading(about="", subjects=(), keep=())
    subjects: list[str] = []
    for reading in readings:
        subjects.extend(s for s in reading.subjects if s not in subjects)
    return MomentReading(
        about=readings[0].about,
        subjects=tuple(subjects),
        keep=tuple(kept[:keep_cap] if keep_cap else kept),
    )


def place_of(assets: list[Any]) -> str | None:
    """Where these photographs were taken, according to their coordinates.

    Never according to what the model reads off a sign. The same episode came
    back named as two different racing circuits on two runs, 900km apart, and
    only one was where the photographs were taken. Reading a name off signage
    is worth having; trusting it is not, so the place is supplied and the
    prompt forbids guessing one.

    A city claimed by a single asset among several is a stray — a photograph
    taken on the way, or a coordinate that drifted — so it takes agreement.
    """
    cities = Counter(
        asset.exif_info.city
        for asset in assets
        if getattr(asset, "exif_info", None) and getattr(asset.exif_info, "city", None)
    )
    if not cities:
        return None
    city, seen = cities.most_common(1)[0]
    return city if seen > 1 or len(cities) == 1 else None


def prompt_for(assets: list[Any]) -> str:
    """The sheet prompt, told where this was and who was in it."""
    where = place_of(assets)
    located = f"\n\nThese were taken in {where}." if where else ""
    numbered = list(enumerate(assets, start=1))
    return SHEET_PROMPT + located + who_was_there(numbered)
