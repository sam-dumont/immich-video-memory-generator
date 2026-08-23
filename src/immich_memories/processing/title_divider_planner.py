"""Timeline dividers between clips: month, year, and location cards."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from immich_memories.processing.assembly_config import AssemblyClip

if TYPE_CHECKING:
    from immich_memories.titles.generator import GeneratedScreen

logger = logging.getLogger(__name__)


class DividerCardGenerator(Protocol):
    """The slice of TitleScreenGenerator the planner needs to render a divider."""

    def generate_month_divider(
        self,
        month: int,
        year: int | None = ...,
        is_birthday_month: bool = ...,
    ) -> GeneratedScreen: ...

    def generate_year_divider(self, year: int) -> GeneratedScreen: ...

    def generate_location_card_screen(
        self, location_name: str, lat: float | None = ..., lon: float | None = ...
    ) -> GeneratedScreen: ...


def _divider_limit(title_settings: Any) -> int | None:
    """Return only a real timeline cap; MagicMock/standalone callers remain uncapped."""
    value = getattr(title_settings, "max_dividers", None)
    return max(0, value) if isinstance(value, int) else None


def parse_clip_date(clip: AssemblyClip) -> date | None:
    """Parse the date from an AssemblyClip."""
    if not clip.date:
        return None
    try:
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%B %d, %Y",
            "%b %d, %Y",
        ):
            try:
                return datetime.strptime(clip.date, fmt).date()
            except ValueError:
                continue
        return datetime.strptime(clip.date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.debug(f"Could not parse date: {clip.date}")
        return None


def detect_month_changes(clips: list[AssemblyClip]) -> list[tuple[int, int, int]]:
    """Detect month changes. Returns [(insert_index, month, year)]."""
    month_changes: list[tuple[int, int, int]] = []
    current_month: tuple[int, int] | None = None
    month_clip_counts: dict[tuple[int, int], int] = {}
    clips_with_dates = 0

    for i, clip in enumerate(clips):
        clip_date = parse_clip_date(clip)
        if clip_date is None:
            continue
        clips_with_dates += 1
        month_key = (clip_date.year, clip_date.month)
        month_clip_counts[month_key] = month_clip_counts.get(month_key, 0) + 1
        if current_month is None or month_key != current_month:
            month_changes.append((i, clip_date.month, clip_date.year))
            logger.debug(f"Month change at clip {i}: {current_month} -> {month_key}")
        current_month = month_key

    logger.info(f"Month detection: {len(month_changes)} changes in {clips_with_dates} clips")
    return month_changes


def detect_year_changes(clips: list[AssemblyClip]) -> list[tuple[int, int]]:
    """Detect year changes. Returns [(insert_index, year)]."""
    year_changes: list[tuple[int, int]] = []
    current_year: int | None = None

    for i, clip in enumerate(clips):
        clip_date = parse_clip_date(clip)
        if clip_date is None:
            continue
        if current_year is None or clip_date.year != current_year:
            year_changes.append((i, clip_date.year))
            if current_year is not None:
                logger.info(f"Year change detected at clip {i}: {current_year} -> {clip_date.year}")
            current_year = clip_date.year

    logger.info(f"Year detection: {len(year_changes)} year changes found")
    return year_changes


class TitleDividerPlanner:
    """Decides which divider cards a clip list gets, and interleaves them.

    One planner serves one memory: the generator that renders the cards and the
    title settings that cap and shape them are fixed for its whole life, so
    every method here only ever takes the clips it is dividing.
    """

    def __init__(self, generator: DividerCardGenerator, title_settings: Any) -> None:
        self._generator = generator
        self._title_settings = title_settings

    def generate_year_dividers(
        self,
        clips: list[AssemblyClip],
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[int, Path]:
        """Generate year divider screens. Returns {year: path}."""
        year_changes = detect_year_changes(clips)
        year_divider_paths: dict[int, Path] = {}
        if not year_changes:
            return year_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating year dividers...")

        limit = _divider_limit(self._title_settings)
        planned_changes = year_changes if limit is None else year_changes[1 : limit + 1]
        for _, year in planned_changes:
            if year not in year_divider_paths:
                divider = self._generator.generate_year_divider(year)
                year_divider_paths[year] = divider.path
                logger.info(f"Generated year divider: {year}")
        return year_divider_paths

    def build_clips_with_year_dividers(
        self,
        clips: list[AssemblyClip],
        year_divider_paths: dict[int, Path],
    ) -> list[AssemblyClip]:
        """Interleave clips with year divider screens."""
        result: list[AssemblyClip] = []
        current_year: int | None = None
        inserted = 0
        limit = _divider_limit(self._title_settings)
        for clip in clips:
            clip_date = parse_clip_date(clip)
            if clip_date:
                if (
                    (limit is current_year is None)
                    or (current_year is not None and clip_date.year != current_year)
                ) and clip_date.year in year_divider_paths:
                    if limit is not None and inserted >= limit:
                        current_year = clip_date.year
                        result.append(clip)
                        continue
                    result.append(
                        AssemblyClip(
                            path=year_divider_paths[clip_date.year],
                            duration=self._title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"year_divider_{clip_date.year}",
                            is_title_screen=True,
                        )
                    )
                    inserted += 1
                current_year = clip_date.year
            result.append(clip)
        return result

    def generate_month_dividers(
        self,
        clips: list[AssemblyClip],
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[tuple[int, int], Path]:
        """Generate month divider screens. Returns {(year, month): path}."""
        month_changes = detect_month_changes(clips)
        month_divider_paths: dict[tuple[int, int], Path] = {}

        if not (self._title_settings.show_month_dividers and month_changes):
            return month_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating month dividers...")

        limit = _divider_limit(self._title_settings)
        planned_changes = month_changes
        if limit is not None:
            planned_changes = month_changes[1 : limit + 1]

        for _, month, year in planned_changes:
            key = (year, month)
            if key not in month_divider_paths:
                is_birthday = (
                    self._title_settings.birthday_month is not None
                    and month == self._title_settings.birthday_month
                )
                divider = self._generator.generate_month_divider(
                    month, year, is_birthday_month=is_birthday
                )
                month_divider_paths[key] = divider.path
                logger.info(
                    f"Generated month divider: {month}/{year}"
                    + (" (birthday!)" if is_birthday else "")
                )
        return month_divider_paths

    def build_clips_with_dividers(
        self,
        clips: list[AssemblyClip],
        month_divider_paths: dict[tuple[int, int], Path],
    ) -> list[AssemblyClip]:
        """Interleave clips with month divider screens."""
        result: list[AssemblyClip] = []
        current_month: tuple[int, int] | None = None
        inserted = 0
        limit = _divider_limit(self._title_settings)

        for clip in clips:
            clip_date = parse_clip_date(clip)
            if clip_date:
                month_key = (clip_date.year, clip_date.month)
                # WHY: skip the first month divider — the intro title already
                # shows the month/year context. Only insert dividers when the
                # month CHANGES (not for the very first clip).
                if (
                    self._title_settings.show_month_dividers
                    and current_month is not None
                    and month_key != current_month
                    and month_key in month_divider_paths
                    and (limit is None or inserted < limit)
                ):
                    result.append(
                        AssemblyClip(
                            path=month_divider_paths[month_key],
                            duration=self._title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"month_divider_{month_key[1]:02d}",
                            is_title_screen=True,
                        )
                    )
                    inserted += 1
                current_month = month_key
            result.append(clip)
        return result

    def make_location_card_clip(
        self,
        name: str,
        cache: dict[str, Path],
        lat: float | None = None,
        lon: float | None = None,
    ) -> AssemblyClip:
        """Return an AssemblyClip for a location card, using cache to avoid duplicates.

        Coordinates turn the card's background from a flat grey panel into a
        satellite map of the place it names. Cached by name, so a place seen
        twice keeps the first card rather than re-rendering the same map.
        """
        if name not in cache:
            card = self._generator.generate_location_card_screen(name, lat=lat, lon=lon)
            cache[name] = card.path
        return AssemblyClip(
            path=cache[name],
            duration=self._title_settings.month_divider_duration,
            date=None,
            asset_id=f"location_{name}",
            is_title_screen=True,
        )

    def build_clips_with_location_dividers(
        self,
        clips: list[AssemblyClip],
        progress_callback: Callable[[float, str], None] | None,
    ) -> list[AssemblyClip]:
        """Insert location cards between clips when location changes (>30km)."""
        from immich_memories.analysis.trip_detection import haversine_km

        if progress_callback:
            progress_callback(0.05, "Generating location cards...")

        result: list[AssemblyClip] = []
        location_card_cache: dict[str, Path] = {}
        prev_lat: float | None = None
        prev_lon: float | None = None
        threshold_km = 30.0
        inserted = 0
        limit = _divider_limit(self._title_settings)

        for clip in clips:
            if clip.latitude is not None and clip.longitude is not None:
                if prev_lat is not None and prev_lon is not None:
                    dist = haversine_km(prev_lat, prev_lon, clip.latitude, clip.longitude)
                    if (
                        dist > threshold_km
                        and clip.location_name
                        and (limit is None or inserted < limit)
                    ):
                        card = self.make_location_card_clip(
                            clip.location_name,
                            location_card_cache,
                            lat=clip.latitude,
                            lon=clip.longitude,
                        )
                        result.append(card)
                        inserted += 1
                        logger.info(f"Location card: {clip.location_name} (dist={dist:.0f}km)")
                prev_lat = clip.latitude
                prev_lon = clip.longitude
            result.append(clip)
        return result

    def select_divider_strategy(
        self,
        clips: list[AssemblyClip],
        progress_callback: Callable[[float, str], None] | None,
        is_trip: bool,
    ) -> list[AssemblyClip]:
        """Select and apply the appropriate divider strategy for clips."""
        if is_trip and getattr(self._title_settings, "show_location_cards", True):
            return self.build_clips_with_location_dividers(clips, progress_callback)

        divider_mode = getattr(self._title_settings, "divider_mode", "month")
        if divider_mode == "year":
            year_divider_paths = self.generate_year_dividers(clips, progress_callback)
            return self.build_clips_with_year_dividers(clips, year_divider_paths)

        if divider_mode == "month" and self._title_settings.show_month_dividers:
            month_divider_paths = self.generate_month_dividers(clips, progress_callback)
            return self.build_clips_with_dividers(clips, month_divider_paths)

        return clips.copy()
