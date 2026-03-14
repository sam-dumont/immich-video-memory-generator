"""Post-pipeline LLM title generation helper.

Called after SmartPipeline.run() completes to populate AppState with a
title suggestion. Fire-and-forget: failures are logged and ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from immich_memories.config import get_config
from immich_memories.titles.llm_titles import generate_title_with_llm

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

logger = logging.getLogger(__name__)


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


def _gather_trip_context(state: AppState) -> _TripContext:
    """Gather trip context: raw daily GPS clusters for the LLM to analyze.

    Shows photo count per location cluster per day so the LLM can detect:
    - Base camp: same cluster appears every day
    - Road trip: different cluster each day, large distances
    - Hiking trail: progressive short-distance moves
    """
    from collections import defaultdict

    from immich_memories.analysis.trip_detection import haversine_km

    ctx = _TripContext()
    if state.memory_type != "trip" or not state.clips:
        return ctx
    try:
        by_date: defaultdict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for clip in state.clips:
            a = clip.asset
            if not a.exif_info or not a.exif_info.latitude:
                continue
            dt = a.local_date_time or a.file_created_at
            city = a.exif_info.city or "Unknown"
            by_date[str(dt.date())].append((city, a.exif_info.latitude, a.exif_info.longitude or 0))

        # Build per-day cluster summaries
        daily: list[str] = []
        for d in sorted(by_date):
            entries = by_date[d]
            # Cluster photos within 5km
            clusters: list[tuple[str, float, float, int]] = []  # city, lat, lon, count
            for city, lat, lon in entries:
                merged = False
                for i, (cc, cl, co, cn) in enumerate(clusters):
                    if haversine_km(lat, lon, cl, co) < 5:
                        clusters[i] = (cc, cl, co, cn + 1)
                        merged = True
                        break
                if not merged:
                    clusters.append((city, lat, lon, 1))
            parts = [f"{c}({n})" for c, _, _, n in sorted(clusters, key=lambda x: -x[3])]
            daily.append(f"{d}: {', '.join(parts)}")
        ctx.daily_locations = daily or None
        ctx.country = _extract_single_country(state)
    except Exception:
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
    """Generate an LLM title suggestion and store it in AppState.

    Best-effort: skips if LLM is unconfigured; logs and continues on failure.
    """
    config = get_config()

    llm_cfg = config.title_llm if config.title_llm and config.title_llm.model else config.llm
    if not llm_cfg.model:
        logger.debug("LLM model not configured — skipping title generation")
        return

    date_range = state.date_range
    if date_range is None:
        logger.debug("No date range — skipping title generation")
        return

    start_date = date_range.start.date()
    end_date = date_range.end.date()

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
            person_names=_gather_person_names(state) or None,
            clip_descriptions=_collect_clip_descriptions(state) or None,
            llm_config=llm_cfg,
        )
    except Exception:
        logger.warning("LLM title generation failed", exc_info=True)
        return

    if suggestion:
        _apply_suggestion(state, suggestion)
