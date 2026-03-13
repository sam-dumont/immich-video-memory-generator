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
        location_change_threshold_km = 30.0

        for clip in clips:
            if clip.latitude is not None and clip.longitude is not None:
                if prev_lat is not None and prev_lon is not None:
                    dist = haversine_km(prev_lat, prev_lon, clip.latitude, clip.longitude)
                    if dist > location_change_threshold_km and clip.location_name:
                        name = clip.location_name
                        if name not in location_card_cache:
                            card = generator.generate_location_card_screen(name)
                            location_card_cache[name] = card.path
                        result.append(
                            AssemblyClip(
                                path=location_card_cache[name],
                                duration=title_settings.month_divider_duration,
                                date=None,
                                asset_id=f"location_{name}",
                                is_title_screen=True,
                            )
                        )
                        logger.info(f"Location card: {name} (dist={dist:.0f}km)")
                prev_lat = clip.latitude
                prev_lon = clip.longitude
            result.append(clip)

        return result
