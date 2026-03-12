"""Title screen integration methods for VideoAssembler.

This mixin provides title screen generation, month divider handling,
date parsing, and the assemble_with_titles entry point.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    TransitionType,
)
from immich_memories.processing.scaling_utilities import (
    aggregate_mood_from_clips,
)

logger = logging.getLogger(__name__)


class AssemblerTitleMixin:
    """Mixin providing title screen integration methods for VideoAssembler."""

    def _parse_clip_date(self, clip: AssemblyClip) -> date | None:
        """Parse the date from an AssemblyClip.

        Args:
            clip: The clip to extract date from.

        Returns:
            Date object or None if not available.
        """
        if not clip.date:
            return None

        try:
            # Try common date formats (ISO first, then human-readable)
            for fmt in [
                "%Y-%m-%d",  # ISO format (preferred)
                "%Y-%m-%dT%H:%M:%S",  # ISO with time
                "%Y-%m-%d %H:%M:%S",  # ISO with time (space)
                "%B %d, %Y",  # Human-readable (e.g., "October 15, 2025")
                "%b %d, %Y",  # Short month (e.g., "Oct 15, 2025")
            ]:
                try:
                    return datetime.strptime(clip.date, fmt).date()
                except ValueError:
                    continue
            # Fallback: try parsing just the first 10 chars as date
            return datetime.strptime(clip.date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            logger.debug(f"Could not parse date: {clip.date}")
            return None

    def _detect_month_changes(
        self,
        clips: list[AssemblyClip],
    ) -> list[tuple[int, int, int]]:
        """Detect where month changes occur in the clip list.

        Args:
            clips: List of clips with dates.

        Returns:
            List of (insert_index, month, year) for each month change.
        """
        month_changes: list[tuple[int, int, int]] = []
        current_month: tuple[int, int] | None = None
        month_clip_counts: dict[tuple[int, int], int] = {}

        # Count clips per month and detect changes (including first month)
        clips_with_dates = 0
        for i, clip in enumerate(clips):
            clip_date = self._parse_clip_date(clip)
            if clip_date is None:
                continue

            clips_with_dates += 1
            month_key = (clip_date.year, clip_date.month)

            # Count clips in this month
            month_clip_counts[month_key] = month_clip_counts.get(month_key, 0) + 1

            # Detect month change OR first month
            if current_month is None or month_key != current_month:
                month_changes.append((i, clip_date.month, clip_date.year))
                if current_month is None:
                    logger.info(f"First month detected at clip {i}: {month_key}")
                else:
                    logger.info(
                        f"Month change detected at clip {i}: {current_month} -> {month_key}"
                    )

            current_month = month_key

        logger.info(
            f"Month detection: {clips_with_dates}/{len(clips)} clips have dates, {len(month_changes)} month changes found"
        )
        if month_clip_counts:
            logger.info(f"Clips per month: {month_clip_counts}")

        return month_changes

    def _get_orientation_from_clips(self, clips: list[AssemblyClip]) -> str:
        """Detect video orientation from clips.

        Args:
            clips: List of clips.

        Returns:
            Orientation string: "landscape", "portrait", or "square".
        """
        portrait_count = 0
        landscape_count = 0

        for clip in clips[:10]:  # Sample first 10 clips
            res = self._get_video_resolution(clip.path)
            if res:
                w, h = res
                if h > w:
                    portrait_count += 1
                elif w > h:
                    landscape_count += 1

        if portrait_count > landscape_count:
            return "portrait"
        elif landscape_count > portrait_count:
            return "landscape"
        return "landscape"  # Default

    def _get_resolution_tier(self, clips: list[AssemblyClip]) -> str:
        """Detect resolution tier from clips.

        Args:
            clips: List of clips.

        Returns:
            Resolution tier: "720p", "1080p", or "4k".
        """
        max_height = 0

        for clip in clips[:10]:  # Sample first 10 clips
            res = self._get_video_resolution(clip.path)
            if res:
                max_height = max(max_height, max(res))

        if max_height >= 2160:
            return "4k"
        elif max_height >= 1080:
            return "1080p"
        return "720p"

    def _detect_year_changes(
        self,
        clips: list[AssemblyClip],
    ) -> list[tuple[int, int]]:
        """Detect where year changes occur in the clip list.

        Args:
            clips: List of clips with dates.

        Returns:
            List of (insert_index, year) for each year change.
        """
        year_changes: list[tuple[int, int]] = []
        current_year: int | None = None

        for i, clip in enumerate(clips):
            clip_date = self._parse_clip_date(clip)
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

    def _generate_year_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[int, Path]:
        """Generate year divider screens for year transitions.

        Args:
            clips: Content clips.
            generator: TitleScreenGenerator instance.
            title_settings: Title screen settings.
            progress_callback: Progress callback.

        Returns:
            Dict of year -> divider video path.
        """
        year_changes = self._detect_year_changes(clips)
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

    def _build_clips_with_year_dividers(
        self,
        clips: list[AssemblyClip],
        year_divider_paths: dict[int, Path],
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with year divider screens.

        Args:
            clips: Content clips.
            year_divider_paths: Generated year divider paths.
            title_settings: Title screen settings.

        Returns:
            List of clips with year dividers inserted at year boundaries.
        """
        result: list[AssemblyClip] = []
        current_year: int | None = None

        for clip in clips:
            clip_date = self._parse_clip_date(clip)
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

    def _generate_month_dividers(
        self,
        clips: list[AssemblyClip],
        generator: Any,
        title_settings: Any,
        progress_callback: Callable[[float, str], None] | None,
    ) -> dict[tuple[int, int], Path]:
        """Generate month divider screens for month transitions.

        Args:
            clips: Content clips.
            generator: TitleScreenGenerator instance.
            title_settings: Title screen settings.
            progress_callback: Progress callback.

        Returns:
            Dict of (year, month) -> divider video path.
        """
        month_changes = self._detect_month_changes(clips)
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

    def _build_clips_with_dividers(
        self,
        clips: list[AssemblyClip],
        month_divider_paths: dict[tuple[int, int], Path],
        title_settings: Any,
    ) -> list[AssemblyClip]:
        """Interleave clips with month divider screens.

        Args:
            clips: Content clips.
            month_divider_paths: Generated month divider paths.
            title_settings: Title screen settings.

        Returns:
            List of clips with dividers inserted at month boundaries.
        """
        result: list[AssemblyClip] = []
        current_month: tuple[int, int] | None = None

        for clip in clips:
            clip_date = self._parse_clip_date(clip)
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

    def assemble_with_titles(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with title screens, month dividers, and ending screen.

        Args:
            clips: List of clips to assemble.
            output_path: Path for output video.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        title_settings = self.settings.title_screens
        if title_settings is None or not title_settings.enabled:
            return self.assemble(clips, output_path, progress_callback)

        try:
            from immich_memories.titles import TitleScreenConfig, TitleScreenGenerator
        except ImportError as e:
            logger.warning(f"Title screens not available: {e}")
            return self.assemble(clips, output_path, progress_callback)

        orientation = self._get_orientation_from_clips(clips)
        resolution_tier = self._get_resolution_tier(clips)
        logger.info(f"Generating title screens ({orientation}, {resolution_tier})")

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

        # 1. Opening title screen
        if progress_callback:
            progress_callback(0.0, "Generating title screen...")

        title_screen = generator.generate_title_screen(
            year=title_settings.year,
            month=title_settings.month,
            start_date=title_settings.start_date,
            end_date=title_settings.end_date,
            person_name=title_settings.person_name,
            birthday_age=title_settings.birthday_age,
        )
        final_clips: list[AssemblyClip] = [
            AssemblyClip(
                path=title_screen.path,
                duration=title_screen.duration,
                date=None,
                asset_id="title_screen",
                is_title_screen=True,
            )
        ]
        logger.info(f"Generated title screen: {title_screen.path}")

        # 2-3. Clips with dividers (month or year based on divider_mode)
        divider_mode = getattr(title_settings, "divider_mode", "month")
        if divider_mode == "year":
            year_divider_paths = self._generate_year_dividers(
                clips, generator, title_settings, progress_callback
            )
            final_clips.extend(
                self._build_clips_with_year_dividers(clips, year_divider_paths, title_settings)
            )
        elif divider_mode == "month" and title_settings.show_month_dividers:
            month_divider_paths = self._generate_month_dividers(
                clips, generator, title_settings, progress_callback
            )
            final_clips.extend(
                self._build_clips_with_dividers(clips, month_divider_paths, title_settings)
            )
        else:
            # No dividers
            final_clips.extend(clips)

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
            return self.assemble(final_clips, output_path, progress_callback)
        finally:
            self.settings.transition = original_transition
