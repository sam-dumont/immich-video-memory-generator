"""Trip map and location card generation mixin for TitleScreenGenerator.

Provides methods for generating trip-specific title screens:
- Map overview with location pins (GPU-accelerated)
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
    ):  # -> GeneratedScreen
        """Generate a trip map overview screen with location pins.

        Uses staticmap for tile rendering, then Taichi GPU pipeline for
        text animation and video encoding.

        Args:
            locations: List of (lat, lon) for map pins.
            title_text: Title overlay (e.g., "TWO WEEKS IN SPAIN, SUMMER 2025").
            subtitle_text: Optional subtitle.

        Returns:
            GeneratedScreen with path to map video.
        """
        from .generator import GeneratedScreen
        from .map_renderer import render_trip_map_array

        width, height = self.config.output_resolution

        # Render map tiles + pins as numpy array (one-time CPU operation)
        map_array = render_trip_map_array(locations, width, height)

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
