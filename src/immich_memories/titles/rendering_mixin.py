"""Rendering mixin for TitleScreenGenerator.

Provides renderer selection logic (GPU Taichi vs CPU PIL) and
GPU-accelerated title video creation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .renderer_pil import create_title_video
from .styles import TitleStyle

# Try to import GPU-accelerated renderer
try:
    from .renderer_taichi import (
        TaichiTitleConfig,
        create_title_video_taichi,
        init_taichi,
    )

    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    create_title_video_taichi = None
    TaichiTitleConfig = None
    init_taichi = None

logger = logging.getLogger(__name__)


class RenderingMixin:
    """Mixin providing renderer selection and GPU title creation.

    Requires the host class to have:
        - self._use_gpu: bool
        - self.config: TitleScreenConfig with use_gpu_rendering
    """

    def _init_gpu_renderer(self) -> None:
        """Initialize GPU renderer if requested and available."""
        self._use_gpu = False
        if self.config.use_gpu_rendering and TAICHI_AVAILABLE:
            backend = init_taichi()
            if backend:
                self._use_gpu = True
                logger.info(f"GPU rendering enabled: {backend}")
            else:
                logger.info("GPU rendering unavailable, falling back to PIL")

    def _create_title_video(
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
    ) -> Path:
        """Create title video using GPU or PIL renderer.

        Automatically selects the appropriate renderer based on availability.
        HDR flag is read from self.config.hdr.

        Args:
            fade_from_white: If True, fade from white at start (for intro title only).
            is_birthday: If True, enable festive birthday particle effects.
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
            )
        else:
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
    ) -> Path:
        """Create title video using GPU-accelerated Taichi renderer."""
        # Map background_type: soft_gradient/vignette use linear, solid uses radial
        gradient_type = "linear" if style.background_type != "radial" else "radial"

        config = TaichiTitleConfig(
            width=width,
            height=height,
            fps=fps,
            duration=duration,
            # Background colors from style
            bg_color1=style.background_colors[0] if style.background_colors else "#FFF5E6",
            bg_color2=style.background_colors[1]
            if len(style.background_colors) > 1
            else style.background_colors[0]
            if style.background_colors
            else "#FFE4CC",
            gradient_angle=float(style.background_angle),
            gradient_type=gradient_type,
            # Text styling
            text_color=style.text_color,
            title_size_ratio=style.title_size_ratio,
            subtitle_size_ratio=style.subtitle_size_ratio
            * style.title_size_ratio,  # Convert relative to absolute
            # Effects
            enable_bokeh=True,  # Bokeh looks good
            enable_shadow=getattr(style, "text_shadow", False),
            # Animated background
            gradient_rotation=10.0 if animated_background else 0.0,
            color_pulse_amount=0.03 if animated_background else 0.0,
            vignette_pulse=0.05 if animated_background else 0.0,
            # Birthday celebration effects
            is_birthday=is_birthday,
        )
        return create_title_video_taichi(
            title, subtitle, output_path, config, fade_from_white, hdr=hdr
        )

    def _create_map_video(
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
        No bokeh/particles — clean map aesthetic.
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
                # No blur — map must stay sharp
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
        else:
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
