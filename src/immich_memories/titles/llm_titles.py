"""LLM-powered title generation for memory videos."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from immich_memories.analysis.llm_query import query_llm

if TYPE_CHECKING:
    from immich_memories.config_models import LLMConfig

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


@dataclass
class TitleSuggestion:
    """LLM-generated title and trip classification."""

    title: str
    subtitle: str | None = None
    trip_type: TripType | None = None
    map_mode: MapMode | None = None
    map_mode_reason: str | None = None


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
    reason = str(data["map_mode_reason"])[:200] if data.get("map_mode_reason") else None

    return TitleSuggestion(
        title=title,
        subtitle=subtitle,
        trip_type=trip_type,  # type: ignore[arg-type]
        map_mode=map_mode,  # type: ignore[arg-type]
        map_mode_reason=reason,
    )


def _sanitize(text: str, max_len: int) -> str:
    """Remove control characters and cap length."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return cleaned[:max_len]


_PROMPT_TEMPLATE: str | None = None
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "title_generation.md"


def _load_prompt_template() -> str:
    """Load prompt template from external file (cached after first load)."""
    global _PROMPT_TEMPLATE  # noqa: PLW0603
    if _PROMPT_TEMPLATE is None:
        _PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


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
) -> str:
    """Build prompt from external template + context data."""
    lang = _LOCALE_NAMES.get(locale, locale.capitalize())

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

    template = _load_prompt_template()
    return (
        template.replace("{lang}", lang)
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
    llm_config: LLMConfig | None = None,
    temperature: float = 0.1,
) -> TitleSuggestion | None:
    """Generate a title using the LLM. Returns None on failure."""
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
    )

    try:
        raw = await query_llm(
            prompt,
            llm_config,
            temperature=temperature,
            max_tokens=8000,
            timeout_seconds=300,
        )
        return parse_title_response(raw)
    except Exception:
        logger.warning("LLM title generation failed", exc_info=True)
        return None
