"""Stable product stance shared by production editorial entry points."""

from __future__ import annotations

from typing import Literal

from immich_memories.timeperiod import DateRange

__all__ = ["build_editorial_brief", "written_subject"]

# These are the generic stances used by the evaluated matrix. Keep them here so
# native generation and validation ask the same editing question. Explicit user
# subjects still replace the base; no case dates, people or assets belong here.
_BASE_BRIEF = (
    "Make the strongest truthful memory this material supports. Read the whole "
    "candidate set before deciding what it is about. Preserve lived relationships, "
    "change, ordinary texture, consequential one-off facts, and experiences that "
    "develop across separated dates. Prefer what is visible on screen over evidence "
    "that merely explains it. Do not infer importance from photographic volume, "
    "metadata richness, or spectacle."
)
_PORTRAIT_BRIEF = (
    "Build a truthful portrait from recurring lived moments rather than a parade "
    "of similar faces. Preserve changes, relationships, activities, places, and "
    "consequential one-off records."
)
_PRODUCT_BASE_BRIEFS = {
    "monthly_highlights": (
        "Distill this month without flattening it into faces or spectacle. Keep "
        "distinct events and ordinary lived texture, prefer action and place when "
        "they carry the experience, and remove repeated views that make the same "
        "contribution."
    ),
    "person_spotlight": _PORTRAIT_BRIEF,
    "multi_person": _PORTRAIT_BRIEF,
    "trip": (
        "Edit this detected trip as an experience of route, place, action, "
        "atmosphere, and relationships. Long or joined trip moments need internal "
        "variation. A person frame cannot substitute for the landscape, activity, "
        "or location that makes the trip leg distinct."
    ),
    "special_day": (
        "Edit this catalogued occasion as one coherent lived event. Preserve the "
        "event's setting, actions, relationships, and progression without filling "
        "with near-identical portraits."
    ),
    "album": (
        "Edit the curated album membership as a coherent memory. Curation raises "
        "prior value but does not make repeated frames independently useful. "
        "Preserve place, action, atmosphere, and relationships across the album."
    ),
    "holiday": (
        "Edit the requested recurring occasion across its dated windows. Preserve "
        "distinct lived occurrences, relevant people and change. Do not assume "
        "adjacent concerts or routine activity are part of the holiday, and do not "
        "fill time with repeated poses or decorations."
    ),
}

_RITUAL_PRODUCTS = frozenset({"holiday", "on_this_day"})
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_PRODUCT_FLAVORS = {
    "holiday": (
        "THIS PRODUCT'S TEXTURE: the festive character of these dates -- the "
        "settings, tables, decorations, lights and rituals that mark them -- is "
        "the subject itself, chosen on purpose; it is never repetition to trim, "
        "and an ordinary frame from these dates earns its place only by carrying "
        "a relationship or a change."
    ),
    "trip": (
        "THIS PRODUCT'S TEXTURE: a trip -- the place itself is a subject. "
        "Landscapes, streets, food, the light of somewhere else, and the group "
        "in that place are the trip's fabric; frames that would read as filler "
        "at home earn their place here. Beach, pool and swimwear are ordinary "
        "trip life on their own -- read intimacy questions against that relaxed "
        "baseline, not a living-room one."
    ),
    "year_in_review": (
        "THIS PRODUCT'S TEXTURE: the span's arc is the subject -- a calendar "
        "year or a birthday-to-birthday year alike. Its distinctive occasions, "
        "firsts and changes are the spine, and the span's special days must be "
        "present. Daily texture supports the arc; it never floods it."
    ),
    "monthly_highlights": (
        "THIS PRODUCT'S TEXTURE: this month as it was actually lived -- its "
        "specific happenings, however small. A quiet month is allowed to look "
        "quiet; never inflate it with filler to seem eventful."
    ),
    "season": (
        "THIS PRODUCT'S TEXTURE: the season's own character -- its light, "
        "weather, outdoor life and activities -- balancing that season's trips "
        "against ordinary home life of the same months. This library lives in "
        "the NORTHERN hemisphere: December is deep winter and June-August is "
        "summer; never read a season name as the other hemisphere's."
    ),
    "person_spotlight": (
        "THIS PRODUCT'S TEXTURE: the named person across this span is the "
        "subject -- their changes, relationships and occasions, spread across "
        "their moments, never concentrated on their best-photographed event. "
        "Plain portraits are punctuation, not content."
    ),
    "multi_person": (
        "THIS PRODUCT'S TEXTURE: the named people are the subject. In a "
        "together-ask every frame ALREADY holds them all -- that filter ran "
        "before anything reached this edit -- so the job is the story of them "
        "together, not re-checking presence. In an each-of-them ask, no named "
        "person goes missing from the wall."
    ),
    "on_this_day": (
        "THIS PRODUCT'S TEXTURE: this exact date returning across the years -- "
        "what repeats (rituals, seasons, places) and what changed between the "
        "years. Each year's take on the date earns one voice."
    ),
    "special_day": (
        "THIS PRODUCT'S TEXTURE: one occasion that mattered, told as its own "
        "story from its first frame to its last. The occasion may overflow the "
        "calendar date -- a birth spanning days, a wedding running past "
        "midnight -- its span is the story's span, not the clock's. Why the "
        "day matters must be visible on the wall, not implied."
    ),
    "album": (
        "THIS PRODUCT'S TEXTURE: an owner-made set -- the album's own "
        "coherence is the thesis. Honor why these belong together; edit within "
        "the owner's intent, never against it."
    ),
}


def written_subject(brief: str) -> str | None:
    """The owner's own ask in a custom film's brief, or None when the brief is the default stance.

    A custom date range with nothing written gets the generic stance as its brief. That text is
    instructions, not a subject: the film is then about its window, like a month or a year.
    """
    text = brief.strip()
    return None if not text or text == _BASE_BRIEF else text


def build_editorial_brief(
    product: str,
    ranges: tuple[DateRange, ...],
    *,
    base: str | None = None,
    hemisphere: Literal["north", "south"] = "north",
) -> str:
    """Compose the exact shared stance from the product and factual scope."""
    product = product.strip()
    base = _PRODUCT_BASE_BRIEFS.get(product, _BASE_BRIEF) if base is None else base.strip()
    if not product or not base:
        raise ValueError("editorial product and base brief must be nonblank")
    if hemisphere not in ("north", "south"):
        raise ValueError("editorial hemisphere must be north or south")
    parts = [base]
    if product in _RITUAL_PRODUCTS and ranges:
        parts.append(_ritual_scope_statement(ranges))
    flavor = _PRODUCT_FLAVORS.get(product)
    if product == "season" and hemisphere == "south":
        flavor = _PRODUCT_FLAVORS["season"].replace(
            "NORTHERN hemisphere: December is deep winter and June-August is summer",
            "SOUTHERN hemisphere: December is summer and June-August is winter",
        )
    if flavor:
        parts.append(
            flavor + " The texture guides WHICH moments and frames carry the memory -- "
            "never HOW MANY; every stated capacity and slot budget stands unchanged."
        )
    return "\n\n".join(parts)


def _ritual_scope_statement(ranges: tuple[DateRange, ...]) -> str:
    years = sorted({window.start.year for window in ranges})
    first = ranges[0]
    start_day = f"{_MONTHS[first.start.month - 1]} {first.start.day}"
    end_day = f"{_MONTHS[first.end.month - 1]} {first.end.day}"
    if start_day == end_day:
        unit, window = "day", start_day
    else:
        unit, window = "days", f"{start_day} to {end_day}"
    return (
        f"WHAT THIS MEMORY IS: the same {unit} each year -- {window} -- across "
        f"{len(years)} years ({years[0]}-{years[-1]}). This scope was chosen "
        "specifically for that yearly return."
    )
