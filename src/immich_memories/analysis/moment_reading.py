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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.contact_sheets import (
    LAYOUT_VERSION,
    ContactSheetPage,
    build_contact_sheets,
)
from immich_memories.analysis.contact_sheets import (
    sheet_layout as _sheet_layout,
)
from immich_memories.analysis.contact_sheets import (
    sheets_of as _sheets_of,
)
from immich_memories.analysis.contact_sheets import (
    tile_sheet as _tile_sheet,
)
from immich_memories.analysis.visual_atlas import AtlasSource, build_visual_atlas

MAX_SHEET_TILES = 120
MAX_SHEET_PX = 2100

if TYPE_CHECKING:
    from PIL.Image import Image

    from immich_memories.config_models_llm import LLMConfig


@dataclass(frozen=True)
class SheetReading:
    """One sheet's answer: what it was, and which tiles to keep."""

    about: str
    subjects: tuple[str, ...]
    keep: tuple[int, ...]


def sheet_layout(count: int) -> tuple[int, int]:
    """Return the shared wide grid dimensions used by legacy moment reading."""
    return _sheet_layout(count)


def sheets_of(items: list[Any], per_sheet: int = MAX_SHEET_TILES) -> list[list[tuple[int, Any]]]:
    """Split legacy moment assets through the shared global-numbering policy."""
    return _sheets_of(items, per_sheet)


def tile_sheet(frames: list[tuple[int, Image | None]]) -> Image | None:
    """Build a legacy sheet through the shared tile renderer."""
    return _tile_sheet(frames)


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
    page: ContactSheetPage,
    llm_config: LLMConfig,
) -> SheetReading | None:
    """Ask about one sheet, or return nothing if it could not be read."""
    import asyncio

    from immich_memories.analysis.llm_query import query_llm

    if page.layout_version != LAYOUT_VERSION or tuple(
        reference.entity_id for reference in page.tile_refs
    ) != tuple(getattr(asset, "id", "") for _number, asset in numbered):
        return None
    answer = asyncio.run(
        query_llm(
            prompt_for([asset for _n, asset in numbered]),
            llm_config,
            temperature=_SHEET_TEMPERATURE,
            max_tokens=SHEET_ANSWER_TOKENS,
            images=[page.jpeg_bytes],
        )
    )
    return read_sheet_verdict(answer)


def read_moment(
    assets: list[Any],
    frames: dict[str, Image],
    llm_config: LLMConfig,
    keep_cap: int | None = None,
    *,
    sheet_output_dir: Path | None = None,
    sheet_recorder: Callable[[ContactSheetPage], None] | None = None,
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
    pages = _moment_pages(assets, frames, sheet_output_dir)
    if sheet_recorder is not None:
        for page in pages:
            sheet_recorder(page)
    readings: list[SheetReading] = []
    kept: list[Any] = []
    for sheet, page in zip(sheets_of(assets), pages, strict=True):
        reading = _read_one_sheet(sheet, page, llm_config)
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


def _moment_pages(
    assets: list[Any], frames: dict[str, Image], output_dir: Path | None
) -> tuple[ContactSheetPage, ...]:
    """Encode all pages once so legacy requests attach precisely the traced bytes."""
    import tempfile

    sources: list[AtlasSource] = []
    for asset in assets:
        asset_id = getattr(asset, "id", "")
        sources.append(AtlasSource(asset=asset, preview_jpeg=_jpeg_for(frames.get(asset_id))))
    destination = output_dir or Path(tempfile.mkdtemp(prefix="moment-sheets-"))
    atlas = build_visual_atlas(sources, frame_cache_dir=None)
    return build_contact_sheets(atlas.tiles, "moment", destination)


def _jpeg_for(frame: Image | None) -> bytes | None:
    if frame is None:
        return None
    import io

    buffer = io.BytesIO()
    frame.save(buffer, "JPEG", quality=_SHEET_QUALITY)
    return buffer.getvalue()


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
