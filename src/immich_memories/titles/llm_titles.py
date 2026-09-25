"""LLM-powered title generation for memory videos."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx

from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.prose_shapes import MAP_MODES, TRIP_TYPES, title_shape
from immich_memories.people.context import PersonPromptContext, load_people_prompt_context

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.config_models_llm import LLMConfig

logger = logging.getLogger(__name__)

TripType = Literal["multi_base", "base_camp", "road_trip", "hiking_trail"]
MapMode = Literal["title_only", "excursions", "overnight_stops"]

_VALID_TRIP_TYPES: set[str] = set(TRIP_TYPES)
_VALID_MAP_MODES: set[str] = set(MAP_MODES)
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
    "pt-BR": "Brazilian Portuguese",
    "pt-PT": "European Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "zh-Hans": "Simplified Chinese",
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
class TitlePrompt:
    """The question put to the reader, and the facts its answer may name.

    ``facts`` is empty for a prompt that makes the reader no such promise, and
    an empty ``facts`` asks for nothing to be checked.
    """

    text: str
    facts: str = ""


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
    # The trip's place as trip naming chose it ("Crete, Greece"): the title must name it.
    place: str | None = None
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
        place=preset.get("location_name") or None,
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
) -> TitlePrompt:
    condition = facts.people_condition or _plain_condition(person_names, facts.person_match)
    known = people_title_facts(person_names, start, end, people_path=facts.people_path)
    if facts.album_name:
        known += f"\nAlbum this film sits in: {facts.album_name}"
    span = span_title_facts(
        start, end, person_names, people_path=facts.people_path, today=facts.today
    )
    return TitlePrompt(
        _load_prompt_template("title_people.md")
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace("{condition}", condition)
        .replace("{people_facts}", known)
        .replace("{span}", span),
        f"{condition}\n{known}\n{span}",
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
) -> TitlePrompt:
    span = span_title_facts(
        start, end, person_names, people_path=facts.people_path, today=facts.today
    )
    known = "\n".join(_occasion_lines(facts, start, end, daily_locations, person_names))
    return TitlePrompt(
        _load_prompt_template("title_occasion.md")
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace("{span}", span)
        .replace("{occasion_facts}", known),
        f"{span}\n{known}",
    )


def _is_trip(memory_type: str) -> bool:
    """Whether this memory is named by the trip prompt, which also classifies the route."""
    return memory_type not in PEOPLE_MEMORY_TYPES and memory_type not in OCCASION_MEMORY_TYPES


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
) -> TitlePrompt:
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
        album_name=known.album_name,
        place=known.place,
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
    album_name: str | None = None,
    place: str | None = None,
) -> TitlePrompt:
    context_lines: list[str] = []
    if place:
        context_lines.append(f"Place (name it, in the title's language): {place}")
    if album_name:
        context_lines.append(f"Album name in Immich: {album_name}")
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

    return TitlePrompt(
        _load_prompt_template()
        .replace("{lang}", lang)
        .replace("{memory_type}", memory_type)
        .replace("{start_date}", start_date)
        .replace("{end_date}", end_date)
        .replace("{duration_days}", str(duration_days))
        .replace("{context_lines}", "\n".join(context_lines))
    )


# Languages spell the same place their own way (Brussels/Bruxelles,
# Gent/Ghent), so a name the facts carry and a name the title writes are the
# same name when they are this close, and different names below it.
_SAME_NAME_RATIO = 0.6


def _name_words(text: str) -> list[str]:
    """Letter-only words, accents folded away so Genève matches Geneve."""
    flattened = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return re.findall(r"[^\W\d_]+", flattened)


def _is_a_known_name(word: str, known: set[str]) -> bool:
    lowered = word.casefold()
    return any(SequenceMatcher(None, lowered, name).ratio() >= _SAME_NAME_RATIO for name in known)


@lru_cache(maxsize=1)
def _calendar_words() -> frozenset[str]:
    """Month and weekday names in every film language: a date spelled out, never a name.

    The facts carry dates as numbers, so without this "Porto in January" reads as a
    title that names something no fact names. Wide names only: an abbreviation such
    as "Jan" is also a first name.
    """
    from babel.dates import get_day_names, get_month_names

    from immich_memories.i18n import SUPPORTED_LOCALES, babel_locale

    words: set[str] = set()
    for code in SUPPORTED_LOCALES:
        where = babel_locale(code)
        for context in ("format", "stand-alone"):
            for names in (
                get_month_names("wide", context, where),
                get_day_names("wide", context, where),
            ):
                for name in names.values():
                    words.update(word.casefold() for word in _name_words(name))
    return frozenset(words)


@lru_cache(maxsize=1)
def _one_word_places() -> dict[str, frozenset[str]]:
    """Countries, islands and regions named in one word, in every film language.

    Folded word -> the places it names (a CLDR code or an English area name),
    so "Chypre", "Cyprus" and "Кипр" are one place and "Chypre" is not "type".
    """
    from immich_memories.i18n import SUPPORTED_LOCALES, babel_locale
    from immich_memories.place_names import area_name_groups

    groups: dict[str, set[str]] = {k: set(v) for k, v in area_name_groups().items()}
    for code in SUPPORTED_LOCALES:
        for territory, name in babel_locale(code).territories.items():
            if territory.isalpha():  # "150" is Europe, "001" the world: not a place visited
                groups.setdefault(territory, set()).add(name)
    places: dict[str, set[str]] = {}
    for key, names in groups.items():
        for name in names:
            if len(words := _name_words(name)) == 1:
                places.setdefault(words[0].casefold(), set()).add(key)
    return {word: frozenset(keys) for word, keys in places.items()}


def _names_a_fact(word: str, known: set[str]) -> bool:
    """Whether `word` is a name the facts carry: the same place, or close in spelling."""
    places = _one_word_places().get(word.casefold())
    if places is None:
        return _is_a_known_name(word, known)
    return any(places & _one_word_places().get(name, frozenset()) for name in known)


def invented_name(line: str, facts: str) -> str | None:
    """The first name this line uses that the facts do not, if it uses one.

    A capitalised word past the first is a proper noun in the languages the
    title screens speak. The first word is capitalised by orthography alone, so
    it counts only when it is a country, island or region's whole name. A place
    passes only when the facts name that same place, in any language. So this
    catches an invented name, not invention: a reworded fact passes, a festival
    or a country nobody recorded does not.
    """
    known = {word.casefold() for word in _name_words(facts)}
    words = _name_words(line)
    named = [word for word in words[1:] if word[:1].isupper()]
    if words and words[0].casefold() in _one_word_places():
        named.insert(0, words[0])
    return next(
        (
            word
            for word in named
            if word.casefold() not in _calendar_words() and not _names_a_fact(word, known)
        ),
        None,
    )


def _refusing_invented_names(
    suggestion: TitleSuggestion | None, facts: str
) -> TitleSuggestion | None:
    """The suggestion, minus whatever part of it names something unrecorded."""
    if suggestion is None or not facts:
        return suggestion
    if invented := invented_name(suggestion.title, facts):
        logger.warning(
            "Title names %r, which no fact names; the template names this memory instead", invented
        )
        return None
    if suggestion.subtitle and (invented := invented_name(suggestion.subtitle, facts)):
        logger.info("Subtitle names %r, which no fact names; dropping the subtitle", invented)
        return replace(suggestion, subtitle=None)
    return suggestion


def names_the_place(title: str, place: str, locale: str) -> bool:
    """Whether `title` names the trip's place, in English or in `locale`.

    Any of the place's own words counts ("Crete" or "Crète" for "Crete,
    Greece", "Utah" for "Utah and Nevada, United States"), spelled as close as
    `_SAME_NAME_RATIO` allows.
    """
    from immich_memories.i18n_places import localise_place

    spellings = f"{place} {localise_place(place, locale) or ''}".replace(" and ", " ")
    known = {word.casefold() for word in _name_words(spellings) if len(word) > 2}
    if not known:
        return True
    # WHY exact under four letters: "été" is as close to "Crete" as "Crète" is.
    return any(
        w.casefold() in known or (len(w) > 3 and _is_a_known_name(w, known))
        for w in _name_words(title)
    )


def _requiring_the_place(
    suggestion: TitleSuggestion | None, place: str | None, locale: str
) -> TitleSuggestion | None:
    if suggestion is None or not place or names_the_place(suggestion.title, place, locale):
        return suggestion
    logger.warning(
        "Title %r does not name the trip's place %r; the template names this trip instead",
        suggestion.title,
        place,
    )
    return None


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
            prompt.text,
            llm_config,
            temperature=temperature,
            max_tokens=8000,
            timeout_seconds=300,
            thinking=True,
            cache_path=cache_path,
            response_format=title_shape(trip=_is_trip(memory_type)),
        )
        suggestion = _refusing_invented_names(parse_title_response(raw), prompt.facts)
        if memory_type in PEOPLE_MEMORY_TYPES or memory_type in OCCASION_MEMORY_TYPES:
            return suggestion
        return _requiring_the_place(suggestion, facts.place if facts else None, locale)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
        logger.warning("LLM title generation failed: %s", e, exc_info=True)
        return None
