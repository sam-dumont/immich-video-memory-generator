"""Title screen insertion: generation, dividers, and assemble_with_titles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.hdr_utilities import has_any_hdr_clip
from immich_memories.processing.scaling_utilities import aggregate_mood_from_clips

logger = logging.getLogger(__name__)

# Type alias for the assemble callback
AssembleFn = Callable[
    [list[AssemblyClip], Path, Callable[[float, str], None] | None],
    Path,
]


class TitleInserter:
    """Inserts title screens, month/year dividers, and location cards into clip lists."""

    def __init__(self, settings: AssemblySettings, prober: FFmpegProber) -> None:
        self.settings = settings
        self.prober = prober

    # ------------------------------------------------------------------
    # Date parsing
    # ------------------------------------------------------------------

    def parse_clip_date(self, clip: AssemblyClip) -> date | None:
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

    # ------------------------------------------------------------------
    # Orientation / resolution detection
    # ------------------------------------------------------------------

    def get_orientation_from_clips(self, clips: list[AssemblyClip]) -> str:
        """Detect dominant video orientation from first 10 clips."""
        portrait_count = 0
        landscape_count = 0
        for clip in clips[:10]:
            res = self.prober.get_video_resolution(clip.path)
            if res:
                w, h = res
                if h > w:
                    portrait_count += 1
                elif w > h:
                    landscape_count += 1
        if portrait_count > landscape_count:
            return "portrait"
        return "landscape"

    def get_resolution_tier(self, clips: list[AssemblyClip]) -> str:
        """Detect resolution tier from first 10 clips."""
        max_height = 0
        for clip in clips[:10]:
            res = self.prober.get_video_resolution(clip.path)
            if res:
                max_height = max(max_height, max(res))
        if max_height >= 2160:
            return "4k"
        elif max_height >= 1080:
            return "1080p"
        return "720p"

    # ------------------------------------------------------------------
    # Month / year change detection
    # ------------------------------------------------------------------

    def detect_month_changes(self, clips: list[AssemblyClip]) -> list[tuple[int, int, int]]:
        """Detect month changes. Returns [(insert_index, month, year)]."""
        month_changes: list[tuple[int, int, int]] = []
        current_month: tuple[int, int] | None = None
        month_clip_counts: dict[tuple[int, int], int] = {}
        clips_with_dates = 0

        for i, clip in enumerate(clips):
            clip_date = self.parse_clip_date(clip)
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

    def detect_year_changes(self, clips: list[AssemblyClip]) -> list[tuple[int, int]]:
        """Detect year changes. Returns [(insert_index, year)]."""
        year_changes: list[tuple[int, int]] = []
        current_year: int | None = None

        for i, clip in enumerate(clips):
            clip_date = self.parse_clip_date(clip)
            if clip_date is None:
                continue
            if current_year is None or clip_date.year != current_year:
                year_changes.append((i, clip_date.year))
                if current_year is not None:
                    logger.info(
                        f"Year change detected at clip {i}: {current_year} -> {clip_date.year}"
                    )
                current_year = clip_date.year

        logger.info(f"Year detection: {len(year_changes)} year changes found")
        return year_changes

    # ------------------------------------------------------------------
    # Divider generation
    # ------------------------------------------------------------------

    def generate_year_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[int, Path]:
        """Generate year divider screens. Returns {year: path}."""
        year_changes = self.detect_year_changes(clips)
        year_divider_paths: dict[int, Path] = {}
        if not year_changes:
            return year_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating year dividers...")

        for _, year in year_changes:
            if year not in year_divider_paths:
                divider = generator.generate_year_divider(year)
                year_divider_paths[year] = divider.path
                logger.info(f"Generated year divider: {year}")
        return year_divider_paths

    def build_clips_with_year_dividers(
        self,
        clips: list[AssemblyClip],
        year_divider_paths: dict[int, Path],
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with year divider screens."""
        result: list[AssemblyClip] = []
        current_year: int | None = None
        for clip in clips:
            clip_date = self.parse_clip_date(clip)
            if clip_date:
                if (
                    current_year is None or clip_date.year != current_year
                ) and clip_date.year in year_divider_paths:
                    result.append(
                        AssemblyClip(
                            path=year_divider_paths[clip_date.year],
                            duration=title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"year_divider_{clip_date.year}",
                            is_title_screen=True,
                        )
                    )
                current_year = clip_date.year
            result.append(clip)
        return result

    def generate_month_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[tuple[int, int], Path]:
        """Generate month divider screens. Returns {(year, month): path}."""
        month_changes = self.detect_month_changes(clips)
        month_divider_paths: dict[tuple[int, int], Path] = {}

        if not (title_settings.show_month_dividers and month_changes):
            return month_divider_paths

        if progress_callback:
            progress_callback(0.05, "Generating month dividers...")

        for _, month, year in month_changes:
            key = (year, month)
            if key not in month_divider_paths:
                is_birthday = (
                    title_settings.birthday_month is not None
                    and month == title_settings.birthday_month
                )
                divider = generator.generate_month_divider(
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
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with month divider screens."""
        result: list[AssemblyClip] = []
        current_month: tuple[int, int] | None = None

        for clip in clips:
            clip_date = self.parse_clip_date(clip)
            if clip_date:
                month_key = (clip_date.year, clip_date.month)
                if (
                    title_settings.show_month_dividers
                    and (current_month is None or month_key != current_month)
                    and month_key in month_divider_paths
                ):
                    result.append(
                        AssemblyClip(
                            path=month_divider_paths[month_key],
                            duration=title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"month_divider_{month_key[1]:02d}",
                            is_title_screen=True,
                        )
                    )
                current_month = month_key
            result.append(clip)
        return result

    # ------------------------------------------------------------------
    # Location dividers (trip memories)
    # ------------------------------------------------------------------

    def make_location_card_clip(
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

    def build_clips_with_location_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
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

        for clip in clips:
            if clip.latitude is not None and clip.longitude is not None:
                if prev_lat is not None and prev_lon is not None:
                    dist = haversine_km(prev_lat, prev_lon, clip.latitude, clip.longitude)
                    if dist > threshold_km and clip.location_name:
                        card = self.make_location_card_clip(
                            clip.location_name, location_card_cache, generator, title_settings
                        )
                        result.append(card)
                        logger.info(f"Location card: {clip.location_name} (dist={dist:.0f}km)")
                prev_lat = clip.latitude
                prev_lon = clip.longitude
            result.append(clip)
        return result

    # ------------------------------------------------------------------
    # Divider strategy selection
    # ------------------------------------------------------------------

    def select_divider_strategy(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
        is_trip: bool,
    ) -> list[AssemblyClip]:
        """Select and apply the appropriate divider strategy for clips."""
        if is_trip and getattr(title_settings, "show_location_cards", True):
            return self.build_clips_with_location_dividers(
                clips, generator, title_settings, progress_callback
            )

        divider_mode = getattr(title_settings, "divider_mode", "month")
        if divider_mode == "year":
            year_divider_paths = self.generate_year_dividers(
                clips, generator, title_settings, progress_callback
            )
            return self.build_clips_with_year_dividers(clips, year_divider_paths, title_settings)

        if divider_mode == "month" and title_settings.show_month_dividers:
            month_divider_paths = self.generate_month_dividers(
                clips, generator, title_settings, progress_callback
            )
            return self.build_clips_with_dividers(clips, month_divider_paths, title_settings)

        return clips.copy()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def assemble_with_titles(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        assemble_fn: AssembleFn,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with title screens, dividers, and ending screen.

        Args:
            clips: List of content clips.
            output_path: Path for output video.
            assemble_fn: Callback to the assembler's assemble() method.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        title_settings = self.settings.title_screens
        if title_settings is None or not title_settings.enabled:
            return assemble_fn(clips, output_path, progress_callback)

        try:
            from immich_memories.titles import TitleScreenConfig, TitleScreenGenerator
        except ImportError as e:
            logger.warning(f"Title screens not available: {e}")
            return assemble_fn(clips, output_path, progress_callback)

        orientation = self.get_orientation_from_clips(clips)
        resolution_tier = self.get_resolution_tier(clips)
        source_has_hdr = has_any_hdr_clip(clips) if self.settings.preserve_hdr else False
        detected_fps = float(self.prober.detect_max_framerate(clips))
        logger.info(
            f"Generating title screens ({orientation}, {resolution_tier}, "
            f"{'HDR' if source_has_hdr else 'SDR'}, {detected_fps:.0f}fps)"
        )

        title_config = TitleScreenConfig(
            enabled=True,
            title_duration=title_settings.title_duration,
            month_divider_duration=title_settings.month_divider_duration,
            ending_duration=title_settings.ending_duration,
            locale=title_settings.locale,
            style_mode=title_settings.style_mode,
            show_month_dividers=title_settings.show_month_dividers,
            month_divider_threshold=title_settings.month_divider_threshold,
            orientation=orientation,
            resolution=resolution_tier,
            fps=detected_fps,
            hdr=source_has_hdr,
            title_override=title_settings.title_override,
            subtitle_override=title_settings.subtitle_override,
        )

        title_output_dir = output_path.parent / ".title_screens"
        title_output_dir.mkdir(parents=True, exist_ok=True)

        mood = title_settings.mood
        if mood is None:
            mood = aggregate_mood_from_clips(clips)
            logger.info(
                f"Auto-detected mood from clips: {mood}"
                if mood
                else "No mood detected from clips, using default style"
            )

        generator = TitleScreenGenerator(
            config=title_config, mood=mood, output_dir=title_output_dir
        )

        # 1. Opening title screen (trip map or standard)
        if progress_callback:
            progress_callback(0.0, "Generating title screen...")

        is_trip = getattr(title_settings, "memory_type", None) == "trip"
        if is_trip and title_settings.trip_locations and title_settings.trip_title_text:
            title_screen = generator.generate_trip_map_screen(
                locations=title_settings.trip_locations,
                title_text=title_settings.trip_title_text,
                home_lat=getattr(title_settings, "home_lat", None),
                home_lon=getattr(title_settings, "home_lon", None),
            )
            logger.info(f"Generated trip map intro: {title_screen.path}")
        else:
            title_screen = generator.generate_title_screen(
                year=title_settings.year,
                month=title_settings.month,
                start_date=title_settings.start_date,
                end_date=title_settings.end_date,
                person_name=title_settings.person_name,
                birthday_age=title_settings.birthday_age,
                content_clip_path=clips[0].path if clips else None,
            )
            logger.info(f"Generated title screen: {title_screen.path}")

        final_clips: list[AssemblyClip] = [
            AssemblyClip(
                path=title_screen.path,
                duration=title_screen.duration,
                date=None,
                asset_id="title_screen",
                is_title_screen=True,
            )
        ]

        # 2-3. Clips with dividers
        content_clips = self.select_divider_strategy(
            clips, generator, title_settings, progress_callback, is_trip
        )
        final_clips.extend(content_clips)

        # 4. Ending screen
        if title_settings.show_ending_screen:
            if progress_callback:
                progress_callback(0.1, "Generating ending screen...")
            video_paths = [clip.path for clip in clips]
            ending_screen = generator.generate_ending_screen(video_clips=video_paths)
            final_clips.append(
                AssemblyClip(
                    path=ending_screen.path,
                    duration=ending_screen.duration,
                    date=None,
                    asset_id="ending_screen",
                    is_title_screen=True,
                )
            )
            logger.info(f"Generated ending screen: {ending_screen.path}")

        # 5. Assemble
        if progress_callback:
            progress_callback(0.15, "Assembling video...")
        logger.info(f"Assembling {len(final_clips)} clips (including title screens)")

        original_transition = self.settings.transition
        if self.settings.transition == TransitionType.CUT:
            logger.info("Upgrading CUT to SMART transitions (title screens require fades)")
            self.settings.transition = TransitionType.SMART

        try:
            return assemble_fn(final_clips, output_path, progress_callback)
        finally:
            self.settings.transition = original_transition
