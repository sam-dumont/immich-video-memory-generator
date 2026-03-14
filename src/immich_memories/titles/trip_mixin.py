"""Trip map and location card generation mixin for TitleScreenGenerator.

Provides methods for generating trip-specific title screens:
- Animated satellite map fly-over (city zoom → pan → city zoom)
- Fallback: static satellite map with pins (when no home coords)
- Location interstitial cards between clips
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class TripScreenMixin:
    """Mixin providing trip map and location card generation.

    Requires the host class to have:
        - self.config: TitleScreenConfig with output_resolution, title_duration, etc.
        - self._use_gpu: bool
        - self._create_map_video(): from RenderingMixin
        - self.output_dir: Path
    """

    def generate_trip_map_screen(
        self,
        locations: list[tuple[float, float]],
        title_text: str,
        subtitle_text: str | None = None,
        home_lat: float | None = None,
        home_lon: float | None = None,
        location_names: list[str] | None = None,
    ):  # -> GeneratedScreen
        """Generate a trip map overview screen.

        When home coordinates are provided, renders an animated satellite
        map fly-over: city-level at departure → zoom out → pan → zoom in
        at destination. Falls back to static map otherwise.

        Args:
            locations: List of (lat, lon) for destination pins.
            title_text: Title overlay text.
            subtitle_text: Optional subtitle (static map only).
            home_lat: Departure latitude (degrees).
            home_lon: Departure longitude (degrees).
            location_names: City names for each destination.

        Returns:
            GeneratedScreen with path to map video.
        """
        if home_lat is not None and home_lon is not None:
            return self._generate_map_fly(locations, title_text, home_lat, home_lon, location_names)
        return self._generate_static_map(locations, title_text, subtitle_text, location_names)

    def _generate_map_fly(
        self,
        destinations: list[tuple[float, float]],
        title_text: str,
        home_lat: float,
        home_lon: float,
        location_names: list[str] | None = None,
    ):  # -> GeneratedScreen
        """Animated satellite map fly-over from home to destinations."""
        from .generator import GeneratedScreen
        from .map_animation import create_map_fly_video

        width, height = self.config.output_resolution
        duration = self.config.title_duration

        output_path = self.output_dir / "trip_map_fly_intro.mp4"
        create_map_fly_video(
            departure=(home_lat, home_lon),
            destinations=destinations,
            title_text=title_text,
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            fps=self.config.fps,
            hold_start=0.5,
            hold_end=1.0,
            hdr=self.config.hdr,
            destination_names=location_names,
        )

        logger.info(f"Map fly animation generated: {output_path}")
        return GeneratedScreen(
            path=output_path,
            duration=duration,
            screen_type="trip_map",
        )

    def _generate_static_map(
        self,
        locations: list[tuple[float, float]],
        title_text: str,
        subtitle_text: str | None,
        location_names: list[str] | None = None,
    ):  # -> GeneratedScreen
        """Static satellite map with pins (fallback when no home coords)."""
        from .generator import GeneratedScreen
        from .map_renderer import render_trip_map_array

        width, height = self.config.output_resolution
        map_array = render_trip_map_array(locations, width, height, location_names=location_names)

        output_path = self.output_dir / "trip_map_intro.mp4"
        self._create_map_video(
            title=title_text,
            subtitle=subtitle_text,
            background_array=map_array,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.title_duration,
            fps=self.config.fps,
        )

        renderer_type = "GPU (Taichi)" if self._use_gpu else "CPU (PIL)"
        logger.info(f"Trip map screen generated [{renderer_type}]: {output_path}")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.title_duration,
            screen_type="trip_map",
        )

    def generate_location_card_screen(
        self,
        location_name: str,
    ):  # -> GeneratedScreen
        """Generate a location interstitial card.

        Args:
            location_name: Location name to display.

        Returns:
            GeneratedScreen with path to card video.
        """
        from .generator import GeneratedScreen
        from .map_renderer import render_location_card

        width, height = self.config.output_resolution
        card_img = render_location_card(location_name, width, height)
        card_array = np.array(card_img, dtype=np.float32) / 255.0

        safe_name = location_name.replace(" ", "_").replace(",", "")[:30]
        output_path = self.output_dir / f"location_{safe_name}.mp4"

        self._create_map_video(
            title=location_name,
            subtitle=None,
            background_array=card_array,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.month_divider_duration,
            fps=self.config.fps,
        )

        logger.info(f"Location card generated: {location_name}")
        return GeneratedScreen(
            path=output_path,
            duration=self.config.month_divider_duration,
            screen_type="location_card",
        )
