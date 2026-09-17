"""LLM-powered title generation for memory videos."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx

from immich_memories.analysis.llm_query import query_llm
from immich_memories.people.context import PersonPromptContext, load_people_prompt_context

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.config_models_llm import LLMConfig

logger = logging.getLogger(__name__)

TripType = Literal["multi_base", "base_camp", "road_trip", "hiking_trail"]
MapMode = Literal["title_only", "excursions", "overnight_stops"]

_VALID_TRIP_TYPES: set[str] = {"multi_base", "base_camp", "road_trip", "hiking_trail"}
_VALID_MAP_MODES: set[str] = {"title_only", "excursions", "overnight_stops"}
_MAX_TITLE_LEN = 80
_MAX_SUBTITLE_LEN = 120

_LOCALE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "nb": "Norwegian",
    "fi": "Finnish",
}

# Which prompt a memory gets. A film about people is named from the family
# record; an occasion from what the occasion was. Everything else is a trip.
PEOPLE_MEMORY_TYPES = frozenset({"person_spotlight", "multi_person"})
OCCASION_MEMORY_TYPES = frozenset(
    {
        "album",
        "holiday",
        "monthly_highlights",
        "on_this_day",
        "season",
        "special_day",
        "year",
        "year_in_review",
    }
)


@dataclass
class TitleSuggestion:
    """LLM-generated title and trip classification."""

    title: str
    subtitle: str | None = None
    trip_type: TripType | None = None
    map_mode: MapMode | None = None


@dataclass(frozen=True)
class MemoryTitleFacts:
    """What the run knows about a memory beyond its dates, places and names.

    Everything here is a recorded fact the title may use: the people condition
    the selection ran on, the name the catalogue or the album owner already
    gave the occasion, and where to read the family record. No story text: the
    readings promote names off banners and signs, and a title may not invent.
    """

    people_condition: str | None = None
    person_match: str = "and"
    occasion_name: str | None = None
    album_name: str | None = None
    holiday: str | None = None
    people_path: Path | None = None
    today: date | None = None


def parse_title_response(raw: str) -> TitleSuggestion | None:
    """Parse LLM JSON response into TitleSuggestion.

    Strips markdown code blocks, validates fields, sanitizes strings.
    Returns None on parse failure.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    # Strip markdown code blocks
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Try direct parse first, then extract JSON from thinking model output
    data = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Thinking models (Qwen3.5) output reasoning before JSON —
        # extract the last JSON object from the response
        json_match = re.search(r'\{[^{}]*"title"[^{}]*\}', text)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError):
                pass
    if data is None:
        logger.warning("LLM title response has no valid JSON: %.100s", raw)
        return None

    if not isinstance(data, dict) or "title" not in data:
        logger.warning("LLM title response missing 'title' key")
        return None

    title = _sanitize(str(data["title"]), _MAX_TITLE_LEN)
    if not title:
        return None

    subtitle = _sanitize(str(data["subtitle"]), _MAX_SUBTITLE_LEN) if data.get("subtitle") else None
    trip_type = data.get("trip_type") if data.get("trip_type") in _VALID_TRIP_TYPES else None
    map_mode = data.get("map_mode") if data.get("map_mode") in _VALID_MAP_MODES else None

    return TitleSuggestion(
        title=title,
        subtitle=subtitle,
        trip_type=trip_type,
        map_mode=map_mode,
    )


def _sanitize(text: str, max_len: int) -> str:
    """Remove control characters and cap length."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return cleaned[:max_len]


_PROMPT_DIR = Path(__file__).parent.parent / "prompts"
_PROMPT_TEMPLATES: dict[str, str] = {}


def _load_prompt_template(name: str = "title_generation.md") -> str:
    """Load prompt template from external file (cached after first load)."""
    if name not in _PROMPT_TEMPLATES:
        _PROMPT_TEMPLATES[name] = (_PROMPT_DIR / name).read_text(encoding="utf-8")
    return _PROMPT_TEMPLATES[name]


def _condition_text(preset: Mapping[str, Any]) -> str | None:
    """The people condition the selection ran on, as one reads it aloud."""
    raw = preset.get("person_expression")
    if not raw:
        return None
    from immich_memories.api.person_expression import PersonExpression

    try:
        return str(PersonExpression.from_dict(raw))
    except (TypeError, ValueError):
        logger.debug("Unreadable people condition; the names carry the film", exc_info=True)
        return None


def _occasion_name(preset: Mapping[str, Any]) -> str | None:
    """What the catalogue saw on this day, months before anybody asked for a film."""
    what = str(preset.get("what") or "").strip()
    kind = str(preset.get("kind") or "").strip()
    if not what:
        return None
    return f"{what} (kind: {kind})" if kind else what


def memory_title_facts(
    preset_params: Mapping[str, Any] | None = None,
    *,
    album_name: str | None = None,
) -> MemoryTitleFacts:
    """Read the facts a run already carries out of its preset parameters."""
    preset = preset_params or {}
    return MemoryTitleFacts(
        people_condition=_condition_text(preset),
        person_match=str(preset.get("person_match") or "and"),
        occasion_name=_occasion_name(preset),
        album_name=album_name or preset.get("album_name") or None,
        holiday=preset.get("holiday") or None,
    )


def _age(born: date, at: date) -> str:
    """How old somebody was, in the unit that still carries meaning at that age."""
    days = (at - born).days
    if days < 0:
        return "not born yet"
    if days < 60:
        return f"{days} days"
    months = (at.year - born.year) * 12 + at.month - born.month - (at.day < born.day)
    if months < 24:
        return f"{months} months"
    return f"{months // 12} years {months % 12} months"


def _birth_date(context: PersonPromptContext | None) -> date | None:
    if context is None or not context.birth_date:
        return None
    try:
        return date.fromisoformat(context.birth_date)
    except ValueError:
        return None


def _people_by_name(people_path: Path | None) -> dict[str, PersonPromptContext]:
    by_name: dict[str, PersonPromptContext] = {}
    for context in load_people_prompt_context(people_path, include_derived=True).values():
        by_name.setdefault(context.name, context)
    return by_name


def _person_line(name: str, context: PersonPromptContext | None, start: date, end: date) -> str:
    born = _birth_date(context)
    if born is None:
        return f"- {name}: birth date unknown"
    return (
        f"- {name}: born {born}; {_age(born, start)} old at the start, {_age(born, end)} at the end"
    )


def _pair_line(name: str, other: str, context: PersonPromptContext | None) -> str:
    kinds = sorted(
        {
            f"{item.kind.replace('-', ' ')} ({item.source})"
            for item in (context.relationships if context else ())
            if item.target_name == other
        }
    )
    return f"- {name} -> {other}: {'; '.join(kinds) or 'no recorded relation'}"


def people_title_facts(
    person_names: Sequence[str],
    start: date,
    end: date,
    *,
    people_path: Path | None = None,
) -> str:
    """One line per person, then the family record for every ordered pair.

    Structure carries this further than instruction does: the model is shown
    how many people are in the film and what the record says about each pair,
    "no recorded relation" included, instead of a role towards somebody who is
    not in the film at all.
    """
    by_name = _people_by_name(people_path)
    lines = [f"People in the film: {len(person_names)}"]
    lines += [_person_line(name, by_name.get(name), start, end) for name in person_names]
    if len(person_names) > 1:
        lines.append("Family record, between the people in the film:")
        lines += [
            _pair_line(name, other, by_name.get(name))
            for name in person_names
            for other in person_names
            if other != name
        ]
    return "\n".join(lines)


def _first_birthday(born: date) -> date | None:
    try:
        return born.replace(year=born.year + 1)
    except ValueError:  # 29 February has no anniversary the following year
        return None


def _birth_notes(name: str, born: date | None, start: date, end: date) -> list[str]:
    if born is None:
        return []
    notes = [f"starts on {name}'s birth date"] if born == start else []
    first = _first_birthday(born)
    if first is not None and end in (first, first - timedelta(days=1)):
        notes.append(f"ends on {name}'s first birthday")
    return notes


def _month_end(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1)


def _calendar_notes(start: date, end: date) -> list[str]:
    if start.year == end.year and (start.month, start.day, end.month, end.day) == (1, 1, 12, 31):
        return [f"the calendar year {start.year}"]
    if (
        (start.year, start.month) == (end.year, end.month)
        and start.day == 1
        and end == _month_end(start)
    ):
        return ["the calendar month"]
    return []


def span_title_facts(
    start: date,
    end: date,
    person_names: Sequence[str] = (),
    *,
    people_path: Path | None = None,
    today: date | None = None,
) -> str:
    """The span, and what it IS: a birth date, a first year, today, a calendar period."""
    notes = [f"{start} to {end} ({(end - start).days} days)"]
    by_name = _people_by_name(people_path) if person_names else {}
    for name in person_names:
        notes += _birth_notes(name, _birth_date(by_name.get(name)), start, end)
    if end == (today or date.today()):
        notes.append("ends today (open-ended)")
    notes += _calendar_notes(start, end)
    return "; ".join(notes)


def _plain_condition(person_names: Sequence[str], match: str) -> str:
    """The condition a plain --person run selected on, written the way one reads it."""
    quoted = [json.dumps(name, ensure_ascii=False) for name in person_names]
    if not quoted:
        return "none recorded"
    if len(quoted) == 1:
        return quoted[0]
    return "(" + (" OR " if match == "or" else " AND ").join(quoted) + ")"


def _people_prompt(
    lang: str,
    memory_type: str,
    start: date,
    end: date,
    person_names: Sequence[str],
    facts: MemoryTitleFacts,
) -> str:
    condition = facts.people_condition or _plain_condition(person_names, facts.person_match)
    known = people_title_facts(person_names, start, end, people_path=facts.people_path)
    if facts.album_name:
        known += f"\nAlbum this film sits in: {facts.album_name}"
    return (
        _load_prompt_template("title_people.md")
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace("{condition}", condition)
        .replace("{people_facts}", known)
        .replace(
            "{span}",
            span_title_facts(
                start, end, person_names, people_path=facts.people_path, today=facts.today
            ),
        )
    )


def _occasion_lines(
    facts: MemoryTitleFacts,
    start: date,
    end: date,
    daily_locations: Sequence[str] | None,
    person_names: Sequence[str],
) -> list[str]:
    lines: list[str] = []
    if facts.occasion_name:
        lines.append(f"The occasion, as catalogued: {facts.occasion_name}")
    if facts.album_name:
        lines.append(f"Album name in Immich: {facts.album_name}")
    if facts.holiday:
        lines.append(f"Holiday: {facts.holiday}")
    if daily_locations:
        lines.append("Places by day:")
        lines += [f"  {entry}" for entry in daily_locations[:30]]
    if person_names:
        lines.extend(
            (
                "People most present in the pictures, in order:",
                people_title_facts(person_names, start, end, people_path=facts.people_path),
            )
        )
    return lines


def _occasion_prompt(
    lang: str,
    memory_type: str,
    start: date,
    end: date,
    facts: MemoryTitleFacts,
    *,
    daily_locations: Sequence[str] | None,
    person_names: Sequence[str],
) -> str:
    return (
        _load_prompt_template("title_occasion.md")
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace(
            "{span}",
            span_title_facts(
                start, end, person_names, people_path=facts.people_path, today=facts.today
            ),
        )
        .replace(
            "{occasion_facts}",
            "\n".join(_occasion_lines(facts, start, end, daily_locations, person_names)),
        )
    )


def build_title_prompt(
    memory_type: str,
    locale: str,
    start_date: str,
    end_date: str,
    duration_days: int,
    *,
    daily_locations: list[str] | None = None,
    country: str | None = None,
    person_names: list[str] | None = None,
    clip_descriptions: list[str] | None = None,
    smart_objects: list[str] | None = None,
    facts: MemoryTitleFacts | None = None,
) -> str:
    """Build the prompt this memory is named from: people, occasion, or trip."""
    lang = _LOCALE_NAMES.get(locale, locale.capitalize())
    known = facts or MemoryTitleFacts()
    names = list(person_names or ())
    if memory_type in PEOPLE_MEMORY_TYPES:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        return _people_prompt(lang, memory_type, start, end, names, known)
    if memory_type in OCCASION_MEMORY_TYPES:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        return _occasion_prompt(
            lang,
            memory_type,
            start,
            end,
            known,
            daily_locations=daily_locations,
            person_names=names,
        )
    return _trip_prompt(
        lang=lang,
        memory_type=memory_type,
        start_date=start_date,
        end_date=end_date,
        duration_days=duration_days,
        daily_locations=daily_locations,
        country=country,
        person_names=person_names,
        clip_descriptions=clip_descriptions,
        smart_objects=smart_objects,
    )


def _trip_prompt(
    *,
    lang: str,
    memory_type: str,
    start_date: str,
    end_date: str,
    duration_days: int,
    daily_locations: list[str] | None = None,
    country: str | None = None,
    person_names: list[str] | None = None,
    clip_descriptions: list[str] | None = None,
    smart_objects: list[str] | None = None,
) -> str:
    context_lines: list[str] = []
    if daily_locations:
        context_lines.append("Daily locations (detect the travel pattern):")
        for loc in daily_locations[:30]:
            context_lines.append(f"  {loc}")
    if country:
        context_lines.append(f"Country: {country}")
    if person_names:
        context_lines.append(f"People: {', '.join(person_names)}")
    if clip_descriptions:
        context_lines.append(f"Clip content: {', '.join(clip_descriptions[:10])}")
    if smart_objects:
        context_lines.append(f"Objects: {', '.join(smart_objects[:20])}")

    return (
        _load_prompt_template()
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace("{start_date}", start_date)
        .replace("{end_date}", end_date)
        .replace("{duration_days}", str(duration_days))
        .replace("{context_lines}", "\n".join(context_lines))
    )


async def generate_title_with_llm(
    memory_type: str,
    locale: str,
    start_date: str,
    end_date: str,
    duration_days: int,
    *,
    daily_locations: list[str] | None = None,
    country: str | None = None,
    person_names: list[str] | None = None,
    clip_descriptions: list[str] | None = None,
    smart_objects: list[str] | None = None,
    facts: MemoryTitleFacts | None = None,
    llm_config: LLMConfig | None = None,
    temperature: float = 0.1,
    cache_path: Path | None = None,
) -> TitleSuggestion | None:
    """Generate a title using the LLM. Returns None on failure.

    With cache_path, a title asked for twice about the same memory is paid for
    once: the prompt carries the dates, places, people, recorded relations and
    clip descriptions, so anything that would change the answer changes the key.
    """
    if llm_config is None:
        return None

    prompt = build_title_prompt(
        memory_type=memory_type,
        locale=locale,
        start_date=start_date,
        end_date=end_date,
        duration_days=duration_days,
        daily_locations=daily_locations,
        country=country,
        person_names=person_names,
        clip_descriptions=clip_descriptions,
        smart_objects=smart_objects,
        facts=facts,
    )

    try:
        raw = await query_llm(
            prompt,
            llm_config,
            temperature=temperature,
            max_tokens=8000,
            timeout_seconds=300,
            thinking=True,
            cache_path=cache_path,
        )
        return parse_title_response(raw)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
        logger.warning("LLM title generation failed: %s", e, exc_info=True)
        return None
