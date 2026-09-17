"""Prepare viewer-facing place labels while preserving source data for maps."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

from immich_memories.analysis.familiar_places import (
    PlaceHistory,
    PlaceObservation,
    valid_coordinates,
)
from immich_memories.i18n import DEFAULT_LOCALE
from immich_memories.i18n_places import localise_place

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams
    from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


def _place_label(
    clip: AssemblyClip,
    history: PlaceHistory,
    home_area: PlaceHistory,
    home_country: str,
    locale: str,
) -> str | None:
    lat, lon = clip.latitude, clip.longitude
    if (
        lat is not None
        and lon is not None
        and (home_area.nearby(lat, lon) or history.is_familiar(lat, lon))
    ):
        return ""
    shown = clip.location_name
    if not shown or not home_country:
        return localise_place(shown, locale)
    # The comparison stays in English on purpose: `home_country` comes from
    # Immich's own EXIF, which is always English, and the translation happens
    # after the home country has been recognised and dropped.
    parts = shown.rsplit(", ", 1)
    if parts[-1].casefold() == home_country.casefold():
        return localise_place(parts[0], locale) if len(parts) == 2 else ""
    return localise_place(shown, locale)


def apply_location_captions(
    clips: list[AssemblyClip],
    history: PlaceHistory,
    *,
    home: tuple[float, float] | None = None,
    locale: str = DEFAULT_LOCALE,
) -> list[AssemblyClip]:
    """Hide home and recurring neighbourhoods; omit only the known home country.

    Country comes from GPS observations around the configured home, never a
    locale or a city-name guess. With no evidence, the original label survives.
    What survives is then read in the film's language.
    """
    home_rows = history.nearby(*home) if home else []
    countries = {row.country.strip() for row in home_rows if row.country.strip()}
    home_country = next(iter(countries)) if len(countries) == 1 else ""
    home_area = PlaceHistory([PlaceObservation(*home, date.min)] if home else [])
    return [
        replace(
            clip,
            caption_location_name=_place_label(clip, history, home_area, home_country, locale),
        )
        for clip in clips
    ]


def prepare_location_captions(
    params: GenerationParams, clips: list[AssemblyClip]
) -> list[AssemblyClip]:
    """Load history only when geographic captions are actually requested."""
    if not params.add_place_overlay or params.privacy_mode:
        return clips
    from immich_memories.analysis.familiar_place_cache import load_place_history

    history = PlaceHistory([])
    if params.client is not None:
        try:
            history = load_place_history(params.client, params.config.cache.cache_path)
        except Exception as error:
            logger.warning(
                "Familiar-place history unavailable (%s); retaining unverified place labels",
                type(error).__name__,
            )
    from immich_memories.processing.clip_caption import resolve_caption_locale

    trips = params.config.trips
    home = (trips.homebase_latitude, trips.homebase_longitude)
    locale = resolve_caption_locale(params.config.title_screens.locale)
    captioned = apply_location_captions(
        clips, history, home=home if valid_coordinates(*home) else None, locale=locale
    )
    return _geocoded_place_names(params, captioned, locale)


def _geocoded_place_names(
    params: GenerationParams, clips: list[AssemblyClip], locale: str
) -> list[AssemblyClip]:
    """Name the places the cut shows in the film's language, one request each.

    Only clips that already carry a place are asked about, so home and the
    neighbourhoods the viewer sees every week are never sent anywhere. The
    library is not walked: this is the cut, which is tens of clips.
    """
    if not params.config.network.geocoding:
        return clips
    from immich_memories.analysis.place_name_cache import (
        PlaceNameCache,
        nominatim_place_reader,
    )

    cache = PlaceNameCache(params.config.cache.cache_path, locale, nominatim_place_reader(locale))
    named = [
        replace(
            clip,
            caption_location_name=cache.name_for(
                clip.latitude, clip.longitude, clip.caption_location_name
            ),
        )
        if clip.caption_location_name and clip.latitude is not None and clip.longitude is not None
        else clip
        for clip in clips
    ]
    cache.flush()
    return named
