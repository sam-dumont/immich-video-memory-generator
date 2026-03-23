"""Rendering service for title screen video creation.

Provides renderer selection logic (GPU Taichi vs CPU PIL) and
video creation methods for titles and map backgrounds.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .styles import TitleStyle
from .video_encoding import create_title_video

if TYPE_CHECKING:
    from .generator import TitleScreenConfig

# Try to import GPU-accelerated renderer
try:
    from .renderer_taichi import (
        TaichiTitleConfig,
        init_taichi,
    )
    from .taichi_video import create_title_video_taichi

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    create_title_video_taichi = None
    TaichiTitleConfig = None
    init_taichi = None

logger = logging.getLogger(__name__)


class RenderingService:
    """Selects GPU or CPU renderer and creates title/map videos."""

    def __init__(self, config: TitleScreenConfig) -> None:
        self.config = config
        self._use_gpu = False
        if config.use_gpu_rendering and TAICHI_AVAILABLE:
            backend = init_taichi()
            if backend:
                self._use_gpu = True
                logger.info(f"GPU rendering enabled: {backend}")
            else:
                logger.info("GPU rendering unavailable, falling back to PIL")

    @property
    def use_gpu(self) -> bool:
        return self._use_gpu

    def create_title_video(
        self,
        title: str,
        subtitle: str | None,
        style: TitleStyle,
        output_path: Path,
        width: int,
        height: int,
        duration: float,
        fps: float,
        animated_background: bool,
        fade_from_white: bool = False,
        is_birthday: bool = False,
        background_image: np.ndarray | None = None,
        content_clip_path: Path | None = None,
        is_ending: bool = False,
        fade_to_white: bool = False,
    ) -> Path:
        """Create title video using GPU or PIL renderer.

        HDR flag is read from self.config.hdr.
        """
        hdr = self.config.hdr
        if self._use_gpu and create_title_video_taichi is not None:
            return self._create_gpu_title(
                title,
                subtitle,
                style,
                output_path,
                width,
                height,
                duration,
                fps,
                animated_background,
                fade_from_white,
                is_birthday,
                hdr=hdr,
                background_image=background_image,
                content_clip_path=content_clip_path,
                is_ending=is_ending,
                fade_to_white=fade_to_white,
            )
        return create_title_video(
            title=title,
            subtitle=subtitle,
            style=style,
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            fps=fps,
            animated_background=animated_background,
            fade_from_white=fade_from_white,
            hdr=hdr,
            background_image=background_image,
        )

    def _create_gpu_title(
        self,
        title: str,
        subtitle: str | None,
        style: TitleStyle,
        output_path: Path,
        width: int,
        height: int,
        duration: float,
        fps: float,
        animated_background: bool,
        fade_from_white: bool,
        is_birthday: bool,
        hdr: bool = True,
        background_image: np.ndarray | None = None,
        content_clip_path: Path | None = None,
        is_ending: bool = False,
        fade_to_white: bool = False,
    ) -> Path:
        """Create title video using GPU-accelerated Taichi renderer."""
        gradient_type = "linear" if style.background_type != "radial" else "radial"
        has_content = background_image is not None or content_clip_path is not None

        # Slow-mo reader for animated content-backed backgrounds
        slowmo_reader = None
        if content_clip_path is not None:
            from .content_background import SlowmoBackgroundReader

            slowmo_reader = SlowmoBackgroundReader(
                content_clip_path,
                width,
                height,
                fps,
                duration,
                hdr=hdr,
            )
            if not slowmo_reader.is_active:
                slowmo_reader = None
                logger.info("Slowmo pipe failed, falling back to static frame")

        config = TaichiTitleConfig(
            width=width,
            height=height,
            fps=fps,
            duration=duration,
            background_image=background_image,
            background_reader=slowmo_reader,
            bg_color1=style.background_colors[0] if style.background_colors else "#1A1A2E",
            bg_color2=style.background_colors[1]
            if len(style.background_colors) > 1
            else style.background_colors[0]
            if style.background_colors
            else "#16213E",
            gradient_angle=float(style.background_angle),
            gradient_type=gradient_type,
            # Text: white, Montserrat, PIL-rendered (pixel-sharp like map titles)
            text_color="#FFFFFF" if has_content else style.text_color,
            title_size_ratio=style.title_size_ratio,
            subtitle_size_ratio=style.subtitle_size_ratio * style.title_size_ratio,
            font_family="Montserrat",
            use_sdf_text=False,
            enable_shadow=False,
            # Blur radius for animated deblur (renderer ramps it from heavy→zero)
            # WHY: blur scales with resolution — 10% of height gives consistent
            # dreamlike effect at any resolution. 1080p=108, 4K=216.
            blur_radius=int(height * 0.10) if slowmo_reader is not None else 20,
            # Keep bokeh on content-backed — user likes them
            enable_bokeh=True,
            # Animated background — disable gradient animation for content-backed
            gradient_rotation=0.0 if has_content else (10.0 if animated_background else 0.0),
            color_pulse_amount=0.0 if has_content else (0.03 if animated_background else 0.0),
            vignette_pulse=0.0 if has_content else (0.05 if animated_background else 0.0),
            vignette_strength=0.15 if has_content else 0.3,
            is_birthday=is_birthday,
            reverse_blur=is_ending,
        )
        try:
            return create_title_video_taichi(
                title,
                subtitle,
                output_path,
                config,
                fade_from_white=fade_from_white,
                fade_to_white=fade_to_white,
                hdr=hdr,
            )
        finally:
            if slowmo_reader is not None:
                slowmo_reader.close()

    def create_map_video(
        self,
        title: str,
        subtitle: str | None,
        background_array: np.ndarray,
        output_path: Path,
        width: int,
        height: int,
        duration: float,
        fps: float,
    ) -> Path:
        """Create a map video using a pre-rendered map as background.

        Uses Taichi GPU for text animation/encoding if available,
        falls back to PIL rendering with the map as static background.
        No bokeh/particles -- clean map aesthetic.
        """
        hdr = self.config.hdr
        if self._use_gpu and create_title_video_taichi is not None:
            # Dim the map so white text pops
            dimmed = background_array * 0.55
            # Target same absolute font size regardless of orientation
            # 0.09 of min(w,h), converted to height-relative ratio
            map_title_ratio = 0.135 * min(width, height) / height
            config = TaichiTitleConfig(
                width=width,
                height=height,
                fps=fps,
                duration=duration,
                background_image=dimmed,
                # Bold white text on dimmed map
                text_color="#FFFFFF",
                title_size_ratio=map_title_ratio,
                subtitle_size_ratio=0.0,
                font_family="Montserrat",
                use_sdf_text=False,  # PIL text = pixel-sharp on maps
                enable_shadow=True,
                shadow_opacity=0.5,
                shadow_offset_ratio=0.004,
                # No blur -- map must stay sharp
                blur_radius=0,
                # No particles/bokeh for maps
                enable_bokeh=False,
                enable_noise=False,
                # Slight edge darkening only
                gradient_rotation=0.0,
                color_pulse_amount=0.0,
                vignette_strength=0.15,
                vignette_pulse=0.0,
            )
            return create_title_video_taichi(
                title, subtitle, output_path, config, fade_from_white=True, hdr=hdr
            )
        # PIL fallback
        return create_title_video(
            title=title,
            subtitle=subtitle,
            style=TitleStyle(
                name="map",
                text_color="#FFFFFF",
                background_type="solid",
                background_colors=["#2D3748"],
            ),
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            fps=fps,
            animated_background=False,
            fade_from_white=True,
            hdr=hdr,
        )
