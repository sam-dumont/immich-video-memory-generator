"""Main title screen generation logic.

Orchestrates creation of opening title screens, month divider screens,
and ending screens via composed services (RenderingService, EndingService,
TripService).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .encoding import (  # noqa: F401
    ORIENTATION_RESOLUTIONS,
    get_resolution_for_orientation,
)
from .ending_service import EndingService
from .rendering_service import RenderingService
from .styles import TitleStyle, get_random_style, get_style_for_mood
from .text_builder import (
    SelectionType,
    TitleInfo,
    generate_month_divider_text,
    generate_title,
    infer_selection_type,
)
from .trip_service import TripService

logger = logging.getLogger(__name__)


@dataclass
class TitleScreenConfig:
    """Configuration for title screen generation."""

    enabled: bool = True

    # Timing
    title_duration: float = 3.5  # seconds
    month_divider_duration: float = 2.0
    ending_duration: float = 7.0
    animation_duration: float = 0.5

    # Localization
    locale: str = "en"

    # Visual style
    style_mode: str = "auto"  # "auto", "random", or specific style name
    animated_background: bool = True  # Enable animated backgrounds by default

    # Decorative elements
    show_decorative_lines: bool = True

    # Color preferences
    avoid_dark_colors: bool = True
    minimum_brightness: int = 100

    # Performance
    use_image_rendering: bool = True
    use_gpu_rendering: bool = True  # Use Taichi GPU when available

    # Font override
    custom_font_path: str | None = None

    # LLM-generated title override (bypasses template generation)
    title_override: str | None = None
    subtitle_override: str | None = None

    # Month dividers
    show_month_dividers: bool = True
    month_divider_threshold: int = 2  # Minimum clips to show month divider

    # Output orientation and resolution
    orientation: str = "landscape"  # "landscape", "portrait", or "square"
    resolution: str = "1080p"  # "720p", "1080p", or "4k"
    fps: float = 30.0  # Matched to assembly target fps by caller; 30 is safe default

    # HDR: match source clips. True = HLG bt2020, False = SDR yuv420p.
    hdr: bool = True

    @property
    def output_resolution(self) -> tuple[int, int]:
        """Get the output resolution based on orientation and resolution settings."""
        return get_resolution_for_orientation(self.orientation, self.resolution)


@dataclass
class GeneratedScreen:
    """Result of generating a title screen."""

    path: Path
    duration: float
    screen_type: str  # "title", "month_divider", "ending"


class TitleScreenGenerator:
    """Generates title screens, month dividers, and ending screens.

    Composes 3 services via constructor injection:
    - RenderingService: GPU/CPU renderer selection and video creation
    - EndingService: fade-to-white ending video generation
    - TripService: trip map screens and location cards

    Attributes:
        config: TitleScreenConfig with timing, resolution, and style settings.
        style: TitleStyle defining colors, fonts, and animation.
        output_dir: Directory where generated videos are saved.
    """

    def __init__(
        self,
        config: TitleScreenConfig | None = None,
        style: TitleStyle | None = None,
        mood: str | None = None,
        output_dir: Path | None = None,
    ):
        self.config = config or TitleScreenConfig()
        self.output_dir = output_dir or Path.cwd() / "title_screens"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._mood = mood  # Store mood for logging

        # Determine style
        self._init_style(style)

        # Apply decorative line preference
        if not self.config.show_decorative_lines:
            self.style.use_line_accent = False

        # Compose services
        self._rendering = RenderingService(self.config)
        self._ending = EndingService(self.style)
        self._trip = TripService(self.config, self._rendering, self.output_dir)

    def _init_style(self, style: TitleStyle | None) -> None:
        """Initialize the visual style based on config and mood."""
        if style is not None:
            self.style = style
            logger.info(f"Using provided style: {self.style.name}")
        elif self.config.style_mode == "auto" and self._mood:
            self.style = get_style_for_mood(self._mood)
            logger.info(f"Using mood-based style: {self.style.name} (mood: {self._mood})")
        elif self.config.style_mode == "random":
            self.style = get_random_style()
            logger.info(f"Using random style: {self.style.name}")
        elif self.config.style_mode not in ("auto", "random"):
            from .styles import PRESET_STYLES

            self.style = PRESET_STYLES.get(self.config.style_mode, get_random_style())
            logger.info(f"Using named style: {self.style.name}")
        else:
            self.style = get_random_style()
            logger.info(f"Using default random style: {self.style.name}")

    def generate_title_screen(
        self,
        *,
        year: int | None = None,
        month: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        person_name: str | None = None,
        birthday_age: int | None = None,
        selection_type: SelectionType | None = None,
    ) -> GeneratedScreen:
        """Generate the opening title screen."""
        if selection_type is None:
            selection_type = infer_selection_type(
                year=year,
                month=month,
                start_date=start_date,
                end_date=end_date,
                birthday_age=birthday_age,
            )

        # Use LLM-generated title if available
        if self.config.title_override:
            title_info = TitleInfo(
                main_title=self.config.title_override,
                subtitle=self.config.subtitle_override or "",
                selection_type=selection_type or SelectionType.CALENDAR_YEAR,
            )
        else:
            title_info = generate_title(
                selection_type,
                year=year,
                month=month,
                start_date=start_date,
                end_date=end_date,
                person_name=person_name,
                birthday_age=birthday_age,
                locale=self.config.locale,
            )

        self._log_title_generation(
            selection_type,
            year,
            month,
            start_date,
            end_date,
            person_name,
            birthday_age,
            title_info,
        )

        width, height = self.config.output_resolution
        output_path = self.output_dir / "title_screen.mp4"

        self._rendering.create_title_video(
            title=title_info.main_title,
            subtitle=title_info.subtitle,
            style=self.style,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.title_duration,
            fps=self.config.fps,
            animated_background=self.config.animated_background,
            fade_from_white=True,
        )

        renderer_type = "GPU (Taichi)" if self._rendering.use_gpu else "CPU (PIL)"
        logger.info(f"Title screen generated [{renderer_type}]: {output_path}")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.title_duration,
            screen_type="title",
        )

    def _log_title_generation(
        self,
        selection_type,
        year,
        month,
        start_date,
        end_date,
        person_name,
        birthday_age,
        title_info,
    ) -> None:
        """Log title screen inputs, generated text, and rendering config."""
        logger.info("=" * 60)
        logger.info("TITLE SCREEN GENERATION")
        logger.info("=" * 60)
        logger.info(f"  Selection type: {selection_type.value}")
        if year:
            logger.info(f"  Year: {year}")
        if month:
            logger.info(f"  Month: {month}")
        if start_date:
            logger.info(f"  Start date: {start_date}")
        if end_date:
            logger.info(f"  End date: {end_date}")
        if person_name:
            logger.info(f"  Person name: {person_name}")
        if birthday_age:
            logger.info(f"  Birthday age: {birthday_age}")
        logger.info("-" * 40)
        logger.info(f'  Main title: "{title_info.main_title}"')
        if title_info.subtitle:
            logger.info(f'  Subtitle: "{title_info.subtitle}"')
        else:
            logger.info("  Subtitle: (none)")
        logger.info("-" * 40)
        logger.info(f"  Style: {self.style.name}")
        logger.info(f"  Font: {self.style.font_family} ({self.style.font_weight})")
        logger.info(f"  Animation: {self.style.animation_preset}")
        logger.info(f"  Background: {self.style.background_type}")
        if self._mood:
            logger.info(f"  Mood: {self._mood}")
        width, height = self.config.output_resolution
        logger.info("-" * 40)
        logger.info(f"  Resolution: {width}x{height}")
        logger.info(f"  FPS: {self.config.fps}")
        logger.info(f"  Duration: {self.config.title_duration}s")
        logger.info(f"  Animated background: {self.config.animated_background}")
        logger.info("=" * 60)

    def generate_month_divider(
        self,
        month: int,
        year: int | None = None,
        is_birthday_month: bool = False,
    ) -> GeneratedScreen:
        """Generate a month divider screen."""
        month_text = generate_month_divider_text(
            month,
            year=year,
            locale=self.config.locale,
        )

        subtitle = None
        animation_preset = "slow_fade"
        if is_birthday_month:
            subtitle = "\U0001f382 \U0001f388 \U0001f389"
            animation_preset = "bounce_in"
            logger.info(f"Adding birthday celebration to month divider: {month_text}")

        divider_style = TitleStyle(
            name=f"{self.style.name}_divider",
            font_family=self.style.font_family,
            font_weight="light",
            title_size_ratio=0.08,
            text_color=self.style.text_color,
            background_type=self.style.background_type,
            background_colors=self.style.background_colors,
            animation_preset=animation_preset,
            use_line_accent=False,
        )

        output_path = self.output_dir / f"month_divider_{month:02d}.mp4"
        width, height = self.config.output_resolution

        self._rendering.create_title_video(
            title=month_text,
            subtitle=subtitle,
            style=divider_style,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.month_divider_duration,
            fps=self.config.fps,
            animated_background=self.config.animated_background,
            is_birthday=is_birthday_month,
        )

        logger.info(f"Generated month divider: {month_text}")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.month_divider_duration,
            screen_type="month_divider",
        )

    def generate_year_divider(
        self,
        year: int,
    ) -> GeneratedScreen:
        """Generate a year divider screen."""
        year_text = str(year)

        divider_style = TitleStyle(
            name=f"{self.style.name}_year_divider",
            font_family=self.style.font_family,
            font_weight="light",
            title_size_ratio=0.10,
            text_color=self.style.text_color,
            background_type=self.style.background_type,
            background_colors=self.style.background_colors,
            animation_preset="slow_fade",
            use_line_accent=False,
        )

        output_path = self.output_dir / f"year_divider_{year}.mp4"
        width, height = self.config.output_resolution

        self._rendering.create_title_video(
            title=year_text,
            subtitle=None,
            style=divider_style,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.month_divider_duration,
            fps=self.config.fps,
            animated_background=self.config.animated_background,
        )

        logger.info(f"Generated year divider: {year_text}")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.month_divider_duration,
            screen_type="year_divider",
        )

    def generate_ending_screen(
        self,
        video_clips: list[Path] | None = None,
        _dominant_color: tuple[int, int, int] | None = None,
    ) -> GeneratedScreen:
        """Generate the ending screen with fade to white."""
        fade_to_color = (255, 255, 255)
        output_path = self.output_dir / "ending_screen.mp4"
        width, height = self.config.output_resolution

        self._ending.create_ending_video(
            output_path=output_path,
            fade_to_color=fade_to_color,
            width=width,
            height=height,
            duration=self.config.ending_duration,
            fps=self.config.fps,
            hdr=self.config.hdr,
        )

        logger.info("Generated ending screen with fade to white")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.ending_duration,
            screen_type="ending",
        )

    def generate_trip_map_screen(
        self,
        locations: list[tuple[float, float]],
        title_text: str,
        subtitle_text: str | None = None,
        home_lat: float | None = None,
        home_lon: float | None = None,
        location_names: list[str] | None = None,
    ) -> GeneratedScreen:
        """Generate a trip map overview screen (delegates to TripService)."""
        return self._trip.generate_trip_map_screen(
            locations=locations,
            title_text=title_text,
            subtitle_text=subtitle_text,
            home_lat=home_lat,
            home_lon=home_lon,
            location_names=location_names,
        )

    def generate_location_card_screen(
        self,
        location_name: str,
    ) -> GeneratedScreen:
        """Generate a location interstitial card (delegates to TripService)."""
        return self._trip.generate_location_card_screen(location_name)

    def generate_all_screens(
        self,
        *,
        year: int | None = None,
        month: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        person_name: str | None = None,
        birthday_age: int | None = None,
        video_clips: list[Path] | None = None,
        months_in_video: list[int] | None = None,
    ) -> dict[str, GeneratedScreen]:
        """Generate all screens (title, month dividers, ending) for a video."""
        screens: dict[str, GeneratedScreen] = {}

        screens["title"] = self.generate_title_screen(
            year=year,
            month=month,
            start_date=start_date,
            end_date=end_date,
            person_name=person_name,
            birthday_age=birthday_age,
        )

        if self.config.show_month_dividers and months_in_video and len(months_in_video) > 1:
            for m in months_in_video:
                screens[f"month_{m:02d}"] = self.generate_month_divider(m, year=year)

        screens["ending"] = self.generate_ending_screen(video_clips=video_clips)

        return screens
