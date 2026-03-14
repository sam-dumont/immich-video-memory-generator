"""Trip-specific assembly methods: location dividers between clips.

Extracted from assembler_titles.py to stay within 500-line limit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


class AssemblerTripMixin:
    """Mixin providing trip location divider insertion for VideoAssembler."""

    def _make_location_card_clip(
        self,
        name: str,
        cache: dict[str, Path],
        generator: Any,
        title_settings: Any,
    ) -> AssemblyClip:
        """Return an AssemblyClip for a location card, using cache to avoid duplicates."""
        if name not in cache:
            card = generator.generate_location_card_screen(name)
            cache[name] = card.path
        return AssemblyClip(
            path=cache[name],
            duration=title_settings.month_divider_duration,
            date=None,
            asset_id=f"location_{name}",
            is_title_screen=True,
        )

    def _build_clips_with_location_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> list[AssemblyClip]:
        """Insert location cards between clips when location changes significantly.

        Uses haversine distance (>30km) to detect location changes.
        """
        from immich_memories.analysis.trip_detection import haversine_km

        if progress_callback:
            progress_callback(0.05, "Generating location cards...")

        result: list[AssemblyClip] = []
        location_card_cache: dict[str, Path] = {}
        prev_lat: float | None = None
        prev_lon: float | None = None
        threshold_km = 30.0

        for clip in clips:
            if clip.latitude is not None and clip.longitude is not None:
                if prev_lat is not None and prev_lon is not None:
                    dist = haversine_km(prev_lat, prev_lon, clip.latitude, clip.longitude)
                    if dist > threshold_km and clip.location_name:
                        card = self._make_location_card_clip(
                            clip.location_name, location_card_cache, generator, title_settings
                        )
                        result.append(card)
                        logger.info(f"Location card: {clip.location_name} (dist={dist:.0f}km)")
                prev_lat = clip.latitude
                prev_lon = clip.longitude
            result.append(clip)

        return result
