"""Post-pipeline LLM title generation helper.

Called after SmartPipeline.run() completes to populate AppState with a
title suggestion. Fire-and-forget: failures are logged and ignored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from immich_memories.cache.judgment_cache import verdicts_beside
from immich_memories.memory_types.registry import MemoryType
from immich_memories.titles.llm_titles import generate_title_with_llm, memory_title_facts
from immich_memories.titles.title_source import TitleSource

if TYPE_CHECKING:
    from datetime import date

    from immich_memories.ui.state import AppState

logger = logging.getLogger(__name__)

# What the template title is built from, when it is more than dates and people.
_TEMPLATE_SOURCES: dict[str | None, TitleSource] = {
    MemoryType.ALBUM: TitleSource.ALBUM,
    MemoryType.SPECIAL_DAY: TitleSource.OCCASION,
    MemoryType.HOLIDAY: TitleSource.OCCASION,
    MemoryType.TRIP: TitleSource.PLACE,
}

# The season a northern-hemisphere span starts in, when the preset did not say.
_SEASON_OF_MONTH = dict.fromkeys((12, 1, 2), "winter") | dict.fromkeys((3, 4, 5), "spring")
_SEASON_OF_MONTH |= dict.fromkeys((6, 7, 8), "summer") | dict.fromkeys((9, 10, 11), "autumn")


def _month_year(day: date, locale: str) -> str:
    from immich_memories.i18n import month_name_forms
    from immich_memories.titles.text_builder import title_pattern

    return title_pattern("month_year", locale, year=day.year, **month_name_forms(day.month, locale))


def _occasion_title(
    memory_type: str | None,
    start: date,
    end: date,
    preset_params: dict | None,
    locale: str,
) -> tuple[str, str | None] | None:
    """Titles that name an occasion rather than the span it happens to cover.

    The span of five Christmases is five years and the span of a special day is
    a few hours; naming either by its ends describes none of what happened.
    Returns None for the types whose span *is* the answer.
    """
    from immich_memories.titles.text_builder import SelectionType, generate_title, title_pattern

    if memory_type == "on_this_day":
        info = generate_title(SelectionType.ON_THIS_DAY, start_date=start, locale=locale)
        return info.main_title, info.subtitle

    if memory_type == "holiday":
        from immich_memories.memory_types.factory import holiday_label

        holiday = (preset_params or {}).get("holiday", "christmas")
        subtitle = title_pattern("on_this_day_subtitle", locale)
        return holiday_label(holiday, end.year, locale), subtitle

    if memory_type == "special_day":
        # The catalogue named this day from the day's own photos, months before
        # anybody asked for a video of it.
        entry = preset_params or {}
        name = (entry.get("title") or "").strip() or (entry.get("what") or "").strip()
        if name:
            return name, (entry.get("subtitle") or "").strip() or None

    return None


def _season_title(start: date, end: date, preset_params: dict | None, locale: str) -> str:
    from immich_memories.titles._text_memory_types import generate_season_title

    season = (preset_params or {}).get("season") or _SEASON_OF_MONTH[start.month]
    return generate_season_title(season, start.year, end.year, None, locale).main_title


def generate_template_title(
    memory_type: str | None,
    start_date: str,
    end_date: str,
    person_names: list[str] | None = None,
    album_name: str | None = None,
    preset_params: dict | None = None,
    locale: str = "en",
    hemisphere: str | None = None,
) -> tuple[str, str | None]:
    """Generate a template-based title from memory type and date range.

    Returns (title, subtitle). Used as fallback when LLM is unavailable. With
    no model this is the title the film opens on, so it is written in the
    film's language, with the catalogue and trip wording a CLI run uses.
    """
    from datetime import date as date_cls

    from immich_memories.processing.clip_caption import resolve_caption_locale

    locale = resolve_caption_locale(locale)
    start = date_cls.fromisoformat(start_date)
    end = date_cls.fromisoformat(end_date)
    year = start.year

    if memory_type == "album" and album_name:
        # Someone already named this album by hand; no template beats that.
        return album_name, _month_year(start, locale)

    if memory_type == "season":
        return _season_title(start, end, preset_params, locale), None

    if memory_type == "person_spotlight" and person_names:
        return f"{person_names[0]} — {year}", None

    if memory_type == "multi_person" and person_names:
        names = " & ".join(person_names)
        return f"{names} — {year}", None

    if memory_type == "trip":
        from immich_memories.generate_privacy import generate_trip_title_text

        # The trip card's own words: a month alone would lose where it went.
        trip = generate_trip_title_text(preset_params or {}, locale)
        return trip or _month_year(start, locale), None

    occasion = _occasion_title(memory_type, start, end, preset_params, locale)
    if occasion is not None:
        return occasion

    from immich_memories.titles.text_builder import _generate_date_range_title

    return _generate_date_range_title(
        start, end, None, locale, hemisphere=hemisphere
    ).main_title, None


@dataclass
class _TripContext:
    daily_locations: list[str] | None = None  # raw daily GPS data for LLM
    country: str | None = None


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


async def _album_of_the_cut(state: AppState) -> str | None:
    """The album most of the cut sits in, when Immich can answer.

    A family day is often named by nothing but the album somebody filed it
    under. Reading that name is not inventing one.
    """
    config = state.config
    if config is None or not config.immich.url or not config.immich.api_key:
        return None
    asset_ids = [
        asset.id for clip in state.get_selected_clips() if (asset := getattr(clip, "asset", None))
    ]
    if not asset_ids or state.date_range is None:
        return None
    from immich_memories.api.album_service import FilmScope
    from immich_memories.api.immich import ImmichClient

    scope = FilmScope(
        start=state.date_range.start,
        end=state.date_range.end,
        pool=len(state.clips) + len(state.photo_assets),
    )

    try:
        async with ImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
        ) as client:
            return await client.album_holding_most(asset_ids, scope=scope)
    except Exception:  # WHY: UI graceful degradation
        logger.debug("Album lookup failed; the title goes without it", exc_info=True)
        return None


def _apply_suggestion(state: AppState, suggestion) -> None:
    """Write TitleSuggestion fields into AppState."""
    state.title_suggestion_title = suggestion.title
    state.title_suggestion_subtitle = suggestion.subtitle
    state.title_suggestion_source = TitleSource.MODEL
    state.title_suggestion_trip_type = suggestion.trip_type
    state.title_suggestion_map_mode = suggestion.map_mode
    logger.info("LLM title generated: %r", suggestion.title)


async def _model_title(
    state: AppState,
    start_date: date,
    end_date: date,
    person_names: list[str] | None,
):
    """Ask the reader to name this memory, or return None when it cannot."""
    config = state.config
    if config is None:
        return None
    llm_cfg = config.title_llm if config.title_llm and config.title_llm.model else config.llm
    if not llm_cfg.model:
        logger.debug("LLM model not configured — using template title")
        return None

    trip = _gather_trip_context(state)
    facts = memory_title_facts(state.memory_preset_params, album_name=state.album_name)
    if facts.album_name is None:
        facts = replace(facts, album_name=await _album_of_the_cut(state))

    try:
        return await generate_title_with_llm(
            memory_type=state.memory_type or "year",
            locale=config.title_screens.locale if config.title_screens else "en",
            start_date=str(start_date),
            end_date=str(end_date),
            duration_days=(end_date - start_date).days,
            cache_path=verdicts_beside(config.cache.cache_path),
            daily_locations=trip.daily_locations,
            country=trip.country,
            person_names=person_names,
            clip_descriptions=[
                d for c in state.get_selected_clips() if (d := getattr(c, "llm_description", None))
            ]
            or None,
            facts=facts,
            llm_config=llm_cfg,
        )
    except Exception:  # WHY: UI graceful degradation
        logger.warning("LLM title generation failed — keeping template title", exc_info=True)
        return None


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
        album_name=state.album_name,
        preset_params=state.memory_preset_params,
        locale=config.title_screens.locale,
        hemisphere=config.trips.hemisphere,
    )
    state.title_suggestion_title = template_title
    state.title_suggestion_subtitle = template_subtitle
    state.title_suggestion_source = _TEMPLATE_SOURCES.get(state.memory_type, TitleSource.FALLBACK)
    if state.memory_type == MemoryType.ALBUM and not state.album_name:
        state.title_suggestion_source = TitleSource.FALLBACK

    if state.memory_type == MemoryType.ALBUM and state.album_name:
        # Matches the CLI, where the album name is a title_override: a name the
        # user typed themselves outranks anything the LLM would invent. Step 3
        # still lets them edit it.
        return

    if state.memory_type == MemoryType.SPECIAL_DAY and template_title:
        # Same rule, different author: the catalogue named this day months ago
        # from the day's own photos. The title LLM sees a handful of clip
        # descriptions, and asking it would rename the occasion.
        return

    suggestion = await _model_title(state, start_date, end_date, person_names)
    if suggestion and suggestion.title and suggestion.title.strip():
        _apply_suggestion(state, suggestion)
