"""Title and subtitle text drawn onto the Taichi frame buffer.

Two paths share the same fade/slide/scale timing: GPU SDF glyphs when a font
atlas is available, and PIL otherwise. The PIL path rasterizes each string once
into an RGBA layer that is uploaded to the device and composited every frame,
so text costs no per-frame transfer either way.
"""

import logging
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from immich_memories.titles.colors import ceil_white_for_hdr
from immich_memories.titles.safe_zones import safe_text_width

if TYPE_CHECKING:
    from .sdf_font import SDFFontAtlas

# Import module for runtime access to compiled kernels.
# Kernels are initially None and compiled lazily by init_taichi().
# Direct `from .taichi_kernels import _func` would capture None at import time,
# so we access them as `taichi_kernels._func` at call time instead.
from . import taichi_kernels
from .taichi_kernels import (
    SDF_AVAILABLE,
    _get_system_font,
    _hex_to_rgb,
    find_font,
    get_cached_atlas,
    layout_text,
)

logger = logging.getLogger(__name__)


def _split_text_for_rendering(draw, text: str, font, max_width: float) -> list[str]:
    """Split text into lines using pixel widths, preferring comma boundaries."""

    def _measure(t: str) -> int:
        bbox = draw.textbbox((0, 0), t, font=font)
        return bbox[2] - bbox[0]

    if _measure(text) <= max_width:
        return [text]

    # Try comma split first
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        parts[0] += ","
        if all(_measure(p) <= max_width for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if _measure(test) > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


def split_title_lines(text: str, max_chars: int) -> list[str]:
    """Split title text into lines, preferring comma boundaries.

    Args:
        text: Title text to split.
        max_chars: Approximate max characters per line.

    Returns:
        List of lines.
    """
    if len(text) <= max_chars:
        return [text]

    # Prefer splitting at comma
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        # Keep comma on first part for visual continuity
        parts[0] += ","
        if all(len(p) <= max_chars for p in parts):
            return parts

    # Word-wrap fallback
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


class TextConfig(Protocol):
    """Title config fields the text renderer reads."""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def duration(self) -> float: ...

    @property
    def text_color(self) -> str: ...

    @property
    def hdr(self) -> bool: ...

    @property
    def title_size_ratio(self) -> float: ...

    @property
    def subtitle_size_ratio(self) -> float: ...

    @property
    def font_family(self) -> str: ...

    @property
    def use_sdf_text(self) -> bool: ...

    @property
    def enable_shadow(self) -> bool: ...

    @property
    def shadow_offset_ratio(self) -> float: ...

    @property
    def shadow_opacity(self) -> float: ...

    @property
    def fade_in_duration(self) -> float: ...

    @property
    def fade_out_duration(self) -> float: ...

    @property
    def slide_distance(self) -> int: ...

    @property
    def scale_from(self) -> float: ...

    @property
    def stagger_delay(self) -> float: ...


class FrameBuffers(Protocol):
    """The device-resident buffers text compositing writes through."""

    @property
    def frame(self) -> Any: ...

    @property
    def temp(self) -> Any: ...


class TitleTextRenderer:
    """Composites animated title and subtitle text onto a GPU frame buffer.

    Picks the SDF path when the config asks for it and an atlas loads,
    otherwise falls back to PIL layers. Both write straight into the caller's
    frame buffer -- nothing is read back to the host.
    """

    def __init__(self, config: TextConfig, buffers: FrameBuffers):
        """Resolve the text color and, when SDF is requested, load the atlas."""
        self.config = config
        self.gpu = buffers
        # WHY (#506): text is the one thing here drawn at graphics white. In an
        # HDR run full white sits above the diffuse white of the picture behind
        # it and the title glows; the ceiling applies to the parsed colour so
        # every text path downstream — SDF, kernel, and the PIL layers — gets it.
        text_color = config.text_color
        if config.hdr:
            text_color = ceil_white_for_hdr(text_color)
        self.text_rgb = _hex_to_rgb(text_color)

        self._title_layer: np.ndarray | None = None
        self._subtitle_layer: np.ndarray | None = None
        self._shadow_layer: np.ndarray | None = None
        # WHY: GPU-cached text layers avoid re-uploading each frame.
        # PIL renders text once → upload to ti.ndarray → reuse across frames.
        self._title_layer_gpu = None
        self._subtitle_layer_gpu = None
        self._shadow_layer_gpu = None
        self._cached_text: tuple[str, str | None] | None = None

        # SDF font atlas (loaded on first text render)
        self._sdf_atlas: SDFFontAtlas | None = None
        self._sdf_atlas_float: np.ndarray | None = None
        self._sdf_atlas_gpu = None
        self.use_sdf = config.use_sdf_text and SDF_AVAILABLE

        if self.use_sdf:
            self._init_sdf_atlas()

    def render(self, t: float, progress: float, title: str, subtitle: str | None):
        """Render title and subtitle text onto the frame."""
        cfg = self.config
        title_anim = self._compute_animation(t, progress, is_subtitle=False)
        # WHY: base size on min(w,h) so portrait text isn't giant.
        # Same approach as map titles (rendering_service.py:180).
        base = min(cfg.width, cfg.height)
        ratio = cfg.title_size_ratio * 0.65 if subtitle else cfg.title_size_ratio
        title_size = int(base * ratio)
        subtitle_size = int(base * cfg.subtitle_size_ratio)

        if self.use_sdf:
            self._render_text_sdf(
                title, subtitle, title_anim, title_size, subtitle_size, t, progress
            )
        else:
            self._render_text_pil(title, subtitle, title_anim, subtitle_size, t, progress)

    def _compute_animation(self, t: float, progress: float, is_subtitle: bool = False) -> dict:
        """Compute animation values for current time."""
        cfg = self.config

        if is_subtitle:
            t = max(0, t - cfg.stagger_delay)

        fade_in_progress = min(1.0, t / cfg.fade_in_duration) if cfg.fade_in_duration > 0 else 1.0

        fade_out_start = cfg.duration - cfg.fade_out_duration
        if t > fade_out_start:
            fade_out_progress = min(1.0, (t - fade_out_start) / cfg.fade_out_duration)
        else:
            fade_out_progress = 0.0

        def ease_out_cubic(x):
            return 1.0 - (1.0 - x) ** 3

        fade_in_eased = ease_out_cubic(fade_in_progress)
        fade_out_eased = ease_out_cubic(fade_out_progress)

        opacity = fade_in_eased * (1.0 - fade_out_eased)
        y_offset = cfg.slide_distance * (1.0 - fade_in_eased) - cfg.slide_distance * fade_out_eased
        scale = cfg.scale_from + (1.0 - cfg.scale_from) * fade_in_eased

        return {"opacity": opacity, "y_offset": y_offset, "scale": scale, "x_offset": 0.0}

    def _render_text_sdf(
        self,
        title: str,
        subtitle: str | None,
        title_anim: dict,
        title_size: int,
        subtitle_size: int,
        t: float,
        progress: float,
    ):
        """Render text using GPU SDF kernels."""
        cfg = self.config
        if cfg.enable_shadow:
            self._render_sdf_text_direct(
                title,
                title_size,
                (0.0, 0.0, 0.0),
                title_anim["opacity"] * cfg.shadow_opacity,
                y_offset=title_anim["y_offset"],
                x_offset=title_anim["x_offset"],
                is_shadow=True,
            )
        self._render_sdf_text_direct(
            title,
            title_size,
            self.text_rgb,
            title_anim["opacity"],
            y_offset=title_anim["y_offset"],
            x_offset=title_anim["x_offset"],
        )
        if subtitle:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            subtitle_y_offset = subtitle_anim["y_offset"] + title_size * 1.3
            self._render_sdf_text_direct(
                subtitle,
                subtitle_size,
                self.text_rgb,
                subtitle_anim["opacity"],
                y_offset=subtitle_y_offset,
                x_offset=subtitle_anim["x_offset"],
            )

    def _render_text_pil(
        self,
        title: str,
        subtitle: str | None,
        title_anim: dict,
        subtitle_size: int,
        t: float,
        progress: float,
    ):
        """Render text using PIL-based layers (GPU-cached)."""
        cfg = self.config
        self._render_text_layers(title, subtitle)

        if cfg.enable_shadow and self._shadow_layer_gpu is not None:
            shadow_offset = max(2, int(cfg.height * cfg.shadow_offset_ratio))
            taichi_kernels._composite_text_with_offset(
                self.gpu.frame,
                self._shadow_layer_gpu,
                self.gpu.temp,
                title_anim["opacity"] * cfg.shadow_opacity,
                title_anim["y_offset"] + shadow_offset,
                title_anim["x_offset"] + shadow_offset,
            )
            taichi_kernels._copy_field_3(self.gpu.temp, self.gpu.frame)

        if self._title_layer_gpu is not None:
            taichi_kernels._composite_text_with_offset(
                self.gpu.frame,
                self._title_layer_gpu,
                self.gpu.temp,
                title_anim["opacity"],
                title_anim["y_offset"],
                title_anim["x_offset"],
            )
            taichi_kernels._copy_field_3(self.gpu.temp, self.gpu.frame)

        if self._subtitle_layer_gpu is not None:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            base = min(cfg.width, cfg.height)
            ratio = cfg.title_size_ratio * 0.65 if subtitle else cfg.title_size_ratio
            pil_title_size = int(base * ratio)
            taichi_kernels._composite_text_with_offset(
                self.gpu.frame,
                self._subtitle_layer_gpu,
                self.gpu.temp,
                subtitle_anim["opacity"],
                subtitle_anim["y_offset"] + pil_title_size * 1.3,
                subtitle_anim["x_offset"],
            )
            taichi_kernels._copy_field_3(self.gpu.temp, self.gpu.frame)

    def _init_sdf_atlas(self):
        """Initialize SDF font atlas for GPU text rendering."""
        if not SDF_AVAILABLE or not find_font:
            logger.warning("SDF font support not available")
            self.use_sdf = False
            return

        font_path = find_font(self.config.font_family)
        if not font_path:
            logger.warning(f"Font '{self.config.font_family}' not found, using fallback")
            font_path = find_font("Helvetica")

        if not font_path:
            logger.warning("No fonts found, falling back to PIL")
            self.use_sdf = False
            return

        atlas_size = 128
        self._sdf_atlas = get_cached_atlas(font_path, atlas_size)
        self._sdf_atlas_float = self._sdf_atlas.texture.astype(np.float32) / 255.0
        # Cache SDF atlas on GPU — loaded once, reused every frame
        import taichi as ti

        self._sdf_atlas_gpu = ti.ndarray(dtype=ti.f32, shape=self._sdf_atlas_float.shape)
        self._sdf_atlas_gpu.from_numpy(self._sdf_atlas_float)
        logger.info(f"SDF atlas loaded: {self._sdf_atlas.texture.shape}")

    def _render_sdf_text_direct(
        self,
        text: str,
        font_size: int,
        color: tuple[float, float, float],
        opacity: float,
        y_offset: float = 0.0,
        x_offset: float = 0.0,
        is_shadow: bool = False,
    ):
        """Render text directly onto frame buffer using SDF GPU kernel."""
        if not self.use_sdf or self._sdf_atlas is None or taichi_kernels._render_sdf_text is None:
            return

        scale = font_size / self._sdf_atlas.font_size
        glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        safe_width = safe_text_width(self.config.width, self.config.height)
        if text_width > safe_width:
            width_scale = safe_width / text_width
            scale = scale * width_scale
            glyph_data, text_width, text_height = layout_text(text, self._sdf_atlas, 0, 0, scale)

        center_x = (self.config.width - text_width) / 2
        center_y = (self.config.height - text_height) / 2 + self._sdf_atlas.ascender * scale / 2

        shadow_offset = 0.0
        if is_shadow:
            shadow_offset = max(2, int(self.config.height * self.config.shadow_offset_ratio))

        smoothing = max(0.05, min(0.2, 0.15 / scale))

        # WHY: glyph_data is small (~8 floats × num_glyphs) and generated
        # per-call from layout_text(). Implicit transfer is negligible.
        atlas = self._sdf_atlas_gpu if self._sdf_atlas_gpu is not None else self._sdf_atlas_float
        taichi_kernels._render_sdf_text(
            self.gpu.frame,
            atlas,
            glyph_data,
            len(glyph_data),
            color[0],
            color[1],
            color[2],
            opacity,
            scale,
            center_x + x_offset + shadow_offset,
            center_y + y_offset + shadow_offset,
            smoothing,
        )

    def _render_text_layer(
        self,
        text: str,
        font_size: int,
        color: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Render text to RGBA numpy array using PIL."""
        w, h = self.config.width, self.config.height
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_path = _get_system_font(self.config.font_family)

        try:
            font = ImageFont.truetype(font_path, font_size)
        except (OSError, ValueError):
            font = ImageFont.load_default()

        safe_width = safe_text_width(w, h)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width > safe_width:
            self._draw_multiline_centered(draw, text, font, font_size, safe_width, w, h, color)
        else:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (w - tw) // 2
            y = (h - th) // 2
            draw.text((x, y), text, font=font, fill=color)

        return np.array(img, dtype=np.float32) / 255.0

    @staticmethod
    def _draw_multiline_centered(
        draw,
        text: str,
        font,
        font_size: int,
        max_width: float,
        width: int,
        height: int,
        color: tuple[int, int, int, int],
    ) -> None:
        """Word-wrap text with comma-aware splitting and draw centered."""
        lines = _split_text_for_rendering(draw, text, font, max_width)
        line_height = int(font_size * 1.2)
        total_h = line_height * len(lines)
        start_y = (height - total_h) // 2
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            x = (width - lw) // 2
            y = start_y + i * line_height
            draw.text((x, y), line, font=font, fill=color)

    def _render_text_layers(self, title: str, subtitle: str | None):
        """Pre-render text layers (cached on GPU)."""
        if self._cached_text == (title, subtitle):
            return

        import taichi as ti

        base = min(self.config.width, self.config.height)
        ratio = self.config.title_size_ratio * 0.65 if subtitle else self.config.title_size_ratio
        title_size = int(base * ratio)
        subtitle_size = int(base * self.config.subtitle_size_ratio)

        tr, tg, tb = self.text_rgb
        text_rgba = (int(tr * 255), int(tg * 255), int(tb * 255), 255)
        shadow_rgba = (0, 0, 0, int(self.config.shadow_opacity * 255))

        self._title_layer = self._render_text_layer(title, title_size, text_rgba)
        self._title_layer_gpu = ti.ndarray(dtype=ti.f32, shape=self._title_layer.shape)
        self._title_layer_gpu.from_numpy(self._title_layer)

        if self.config.enable_shadow:
            self._shadow_layer = self._render_text_layer(title, title_size, shadow_rgba)
            self._shadow_layer_gpu = ti.ndarray(dtype=ti.f32, shape=self._shadow_layer.shape)
            self._shadow_layer_gpu.from_numpy(self._shadow_layer)

        if subtitle:
            self._subtitle_layer = self._render_text_layer(subtitle, subtitle_size, text_rgba)
            self._subtitle_layer_gpu = ti.ndarray(dtype=ti.f32, shape=self._subtitle_layer.shape)
            self._subtitle_layer_gpu.from_numpy(self._subtitle_layer)
        else:
            self._subtitle_layer = None
            self._subtitle_layer_gpu = None

        self._cached_text = (title, subtitle)
