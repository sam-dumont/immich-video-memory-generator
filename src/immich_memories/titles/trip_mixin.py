"""Trip map and location card generation mixin for TitleScreenGenerator.

Provides methods for generating trip-specific title screens:
- Animated globe fly-over (GPU-accelerated, Relive-style)
- Fallback: static satellite map with pins
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
    ):  # -> GeneratedScreen
        """Generate a trip map overview screen.

        When GPU is available and homebase is set, renders an animated
        3D globe fly-over from home to destinations (Relive-style).
        Falls back to static satellite map with pins otherwise.

        Args:
            locations: List of (lat, lon) for map pins / destinations.
            title_text: Title overlay text.
            subtitle_text: Optional subtitle.
            home_lat: Homebase latitude (degrees) for globe animation.
            home_lon: Homebase longitude (degrees) for globe animation.

        Returns:
            GeneratedScreen with path to map video.
        """
        if self._use_gpu and home_lat is not None and home_lon is not None:
            return self._generate_globe_animation(locations, title_text, home_lat, home_lon)
        return self._generate_static_map(locations, title_text, subtitle_text)

    def _generate_globe_animation(
        self,
        locations: list[tuple[float, float]],
        title_text: str,
        home_lat: float,
        home_lon: float,
    ):  # -> GeneratedScreen
        """Animated 3D globe fly-over from home to destinations."""
        from .generator import GeneratedScreen
        from .globe_renderer import generate_camera_keyframes
        from .globe_video import create_globe_animation_video
        from .map_renderer import render_equirectangular_map

        width, height = self.config.output_resolution
        duration = self.config.title_duration

        # Compute centroid of all locations for texture center
        all_lats = [home_lat] + [lat for lat, _ in locations]
        all_lons = [home_lon] + [lon for _, lon in locations]
        center_lat = sum(all_lats) / len(all_lats)
        center_lon = sum(all_lons) / len(all_lons)

        # Fetch equirectangular satellite texture
        tex_w = min(width, 1440)  # Cap texture size for performance
        tex_h = tex_w // 2
        texture = render_equirectangular_map(
            center_lat=center_lat,
            center_lon=center_lon,
            width=tex_w,
            height=tex_h,
        )

        # Generate camera keyframes: home → destinations
        keyframes = generate_camera_keyframes(home_lat, home_lon, locations)

        output_path = self.output_dir / "trip_globe_intro.mp4"
        create_globe_animation_video(
            texture=texture,
            keyframes=keyframes,
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            fps=self.config.fps,
            hold_start=0.5,
            hold_end=1.0,
        )

        logger.info(f"Globe animation generated: {output_path}")
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
    ):  # -> GeneratedScreen
        """Static satellite map with pins (fallback when globe unavailable)."""
        from .generator import GeneratedScreen
        from .map_renderer import render_trip_map_array

        width, height = self.config.output_resolution
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
