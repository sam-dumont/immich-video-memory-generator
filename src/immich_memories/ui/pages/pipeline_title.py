"""Post-pipeline LLM title generation helper.

Called after SmartPipeline.run() completes to populate AppState with a
title suggestion. Fire-and-forget: failures are logged and ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from immich_memories.titles.llm_titles import generate_title_with_llm

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

logger = logging.getLogger(__name__)

# Month names for template titles (avoids locale dependency)
_MONTH_NAMES = [
    "",
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
]

# Season detection from month ranges
_SEASON_MAP = {
    (12, 1, 2): "Winter",
    (3, 4, 5): "Spring",
    (6, 7, 8): "Summer",
    (9, 10, 11): "Fall",
}


def _detect_season(start_month: int) -> str:
    """Return season name from start month."""
    for months, name in _SEASON_MAP.items():
        if start_month in months:
            return name
    return "Memories"


def generate_template_title(
    memory_type: str | None,
    start_date: str,
    end_date: str,
    person_names: list[str] | None = None,
) -> tuple[str, str | None]:
    """Generate a template-based title from memory type and date range.

    Returns (title, subtitle). Used as fallback when LLM is unavailable.
    """
    from datetime import date as date_cls

    start = date_cls.fromisoformat(start_date)
    end = date_cls.fromisoformat(end_date)
    year = start.year

    if memory_type in ("year_in_review", "year"):
        return f"Year in Review {year}", None

    if memory_type == "season":
        season = _detect_season(start.month)
        return f"{season} {year}", f"{_MONTH_NAMES[start.month]} \u2013 {_MONTH_NAMES[end.month]}"

    if memory_type == "person_spotlight" and person_names:
        return f"{person_names[0]} \u2014 {year}", None

    if memory_type == "multi_person" and person_names:
        names = " & ".join(person_names)
        return f"{names} \u2014 {year}", None

    if memory_type == "monthly_highlights":
        return f"{_MONTH_NAMES[start.month]} {year}", None

    if memory_type == "trip":
        return f"{_MONTH_NAMES[start.month]} {year} Trip", f"{start_date} \u2013 {end_date}"

    if memory_type == "on_this_day":
        return f"On This Day \u2014 {_MONTH_NAMES[start.month]} {start.day}", None

    # Fallback for unknown types
    span_months = (end.year - start.year) * 12 + (end.month - start.month)
    if span_months >= 10:
        return f"Memories {year}", None
    return (
        f"{_MONTH_NAMES[start.month]} \u2013 {_MONTH_NAMES[end.month]} {year}",
        None,
    )


@dataclass
class _TripContext:
    daily_locations: list[str] | None = None  # raw daily GPS data for LLM
    country: str | None = None


def _collect_clip_descriptions(state: AppState) -> list[str]:
    """Extract LLM descriptions from analysis cache for selected clips."""
    if not state.analysis_cache or not state.selected_clip_ids:
        return []

    descriptions: list[str] = []
    for asset_id in state.selected_clip_ids:
        analysis = state.analysis_cache.get_analysis(asset_id)
        if analysis and analysis.segments:
            best = analysis.get_best_segment()
            if best and best.llm_description:
                descriptions.append(best.llm_description)
    return descriptions


def _gather_person_names(state: AppState) -> list[str]:
    """Get person names from selected person or preset params."""
    if state.selected_person and state.selected_person.name:
        return [state.selected_person.name]
    if state.memory_preset_params.get("person_names"):
        return list(state.memory_preset_params["person_names"])
    return []


def _group_clips_by_date(
    clips: list,
) -> dict[str, list[tuple[str, float, float]]]:
    """Group clip GPS coordinates by date string."""
    from collections import defaultdict

    by_date: defaultdict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for clip in clips:
        a = clip.asset
        if not a.exif_info or not a.exif_info.latitude:
            continue
        dt = a.local_date_time or a.file_created_at
        city = a.exif_info.city or "Unknown"
        by_date[str(dt.date())].append((city, a.exif_info.latitude, a.exif_info.longitude or 0))
    return dict(by_date)


def _cluster_day_entries(
    entries: list[tuple[str, float, float]],
) -> list[tuple[str, float, float, int]]:
    """Cluster GPS entries within 5km and return (city, lat, lon, count)."""
    from immich_memories.analysis.trip_detection import haversine_km

    clusters: list[tuple[str, float, float, int]] = []
    for city, lat, lon in entries:
        merged = False
        for i, (cc, cl, co, cn) in enumerate(clusters):
            if haversine_km(lat, lon, cl, co) < 5:
                clusters[i] = (cc, cl, co, cn + 1)
                merged = True
                break
        if not merged:
            clusters.append((city, lat, lon, 1))
    return clusters


def _build_daily_summaries(
    by_date: dict[str, list[tuple[str, float, float]]],
) -> list[str]:
    """Build per-day cluster summary strings from grouped GPS data."""
    daily: list[str] = []
    for d in sorted(by_date):
        clusters = _cluster_day_entries(by_date[d])
        parts = [f"{c}({n})" for c, _, _, n in sorted(clusters, key=lambda x: -x[3])]
        daily.append(f"{d}: {', '.join(parts)}")
    return daily


def _gather_trip_context(state: AppState) -> _TripContext:
    """Gather trip context: raw daily GPS clusters for the LLM to analyze.

    Shows photo count per location cluster per day so the LLM can detect:
    - Base camp: same cluster appears every day
    - Road trip: different cluster each day, large distances
    - Hiking trail: progressive short-distance moves
    """
    ctx = _TripContext()
    if state.memory_type != "trip" or not state.clips:
        return ctx
    try:
        by_date = _group_clips_by_date(state.clips)
        daily = _build_daily_summaries(by_date)
        ctx.daily_locations = daily or None
        ctx.country = _extract_single_country(state)
    except Exception:  # WHY: UI graceful degradation
        logger.debug("Trip context gathering failed", exc_info=True)
    return ctx


def _extract_single_country(state: AppState) -> str | None:
    """Return the country name only when all clips share the same country."""
    seen: list[str] = []
    for clip in state.clips:
        c = clip.asset.exif_info.country if clip.asset.exif_info else None
        if c and c not in seen:
            seen.append(c)
    return seen[0] if len(seen) == 1 else None


def _apply_suggestion(state: AppState, suggestion) -> None:
    """Write TitleSuggestion fields into AppState."""
    state.title_suggestion_title = suggestion.title
    state.title_suggestion_subtitle = suggestion.subtitle
    state.title_suggestion_trip_type = suggestion.trip_type
    state.title_suggestion_map_mode = suggestion.map_mode
    logger.info("LLM title generated: %r", suggestion.title)


async def generate_title_after_pipeline(state: AppState) -> None:
    """Generate a title suggestion and store it in AppState.

    First applies a template-based fallback title, then attempts LLM
    generation. LLM result overwrites template on success.
    """
    config = state.config
    if config is None:
        logger.debug("Config not initialized — skipping title generation")
        return

    date_range = state.date_range
    if date_range is None:
        logger.debug("No date range — skipping title generation")
        return

    start_date = date_range.start.date()
    end_date = date_range.end.date()

    # Step 1: Apply template fallback (always runs)
    person_names = _gather_person_names(state) or None
    template_title, template_subtitle = generate_template_title(
        memory_type=state.memory_type,
        start_date=str(start_date),
        end_date=str(end_date),
        person_names=person_names,
    )
    state.title_suggestion_title = template_title
    state.title_suggestion_subtitle = template_subtitle

    # Step 2: Try LLM (overwrites template on success)
    llm_cfg = config.title_llm if config.title_llm and config.title_llm.model else config.llm
    if not llm_cfg.model:
        logger.debug("LLM model not configured — using template title")
        return

    trip = _gather_trip_context(state)
    locale = config.title_screens.locale if config.title_screens else "en"

    try:
        suggestion = await generate_title_with_llm(
            memory_type=state.memory_type or "year",
            locale=locale,
            start_date=str(start_date),
            end_date=str(end_date),
            duration_days=(end_date - start_date).days,
            daily_locations=trip.daily_locations,
            country=trip.country,
            person_names=person_names,
            clip_descriptions=_collect_clip_descriptions(state) or None,
            llm_config=llm_cfg,
        )
    except Exception:  # WHY: UI graceful degradation
        logger.warning("LLM title generation failed — keeping template title", exc_info=True)
        return

    # Only overwrite if LLM produced a non-empty title
    if suggestion and suggestion.title and suggestion.title.strip():
        _apply_suggestion(state, suggestion)
