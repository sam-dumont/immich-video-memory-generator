"""Title and subtitle text drawn onto the GPU frame buffer.

Two paths share the same fade/slide/scale timing: GPU SDF glyphs when a font
atlas is available, and PIL otherwise. The PIL path rasterizes each string once
into an RGBA layer that is uploaded to the device and composited every frame,
so text costs no per-frame transfer either way.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from immich_memories.titles.colors import ceil_white_for_hdr
from immich_memories.titles.fonts import font_covering
from immich_memories.titles.safe_zones import safe_text_width
from immich_memories.titles.text_layout import (
    TextStack,
    shrink_sizes,
    stack_text_blocks,
    text_blocks_overlap,
)

if TYPE_CHECKING:
    from .sdf_font import SDFFontAtlas

# Import module for runtime access to compiled kernels.
# Kernels are initially None and compiled lazily by init_kernels().
# Direct `from .kernels import _func` would capture None at import time,
# so we access them as `kernels._func` at call time instead.
from . import kernels
from .gpu_kernel_backend import ti
from .kernels import (
    SDF_AVAILABLE,
    _get_system_font,
    _hex_to_rgb,
    find_font,
    get_cached_atlas,
    layout_text,
)

logger = logging.getLogger(__name__)

# How many times a plan may shrink after the gate reports ink on ink. The layout
# already leaves a gap, so this only ever answers a face drawn taller than the
# line height it was measured at; one or two steps close that.
_OVERLAP_ATTEMPTS = 4


def _single_line(_text: str, _font_size: int) -> int:
    """Line count on the SDF path, which scales a string to fit instead of wrapping it."""
    return 1


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
    def title_subtitle_gap_ratio(self) -> float: ...

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


@dataclass(frozen=True)
class TextPlan:
    """The rasterized title and subtitle layers, and where each one goes."""

    stack: TextStack
    title_layer: np.ndarray
    subtitle_layer: np.ndarray | None
    shadow_layer: np.ndarray | None


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

        # WHY: GPU-cached text layers avoid re-uploading each frame.
        # PIL renders text once → upload to ti.ndarray → reuse across frames.
        self._title_layer_gpu = None
        self._subtitle_layer_gpu = None
        self._shadow_layer_gpu = None
        self._cached_text: tuple[str, str | None] | None = None
        self._text_stack: TextStack | None = None

        # SDF font atlas (loaded on first text render)
        self._sdf_atlas: SDFFontAtlas | None = None
        self._sdf_atlas_float: np.ndarray | None = None
        self._sdf_atlas_gpu = None
        self.use_sdf = config.use_sdf_text and SDF_AVAILABLE

        if self.use_sdf:
            self._init_sdf_atlas()

    def render(self, t: float, progress: float, title: str, subtitle: str | None):
        """Render title and subtitle text onto the frame."""
        title_anim = self._compute_animation(t, progress, is_subtitle=False)
        if self.use_sdf and self._sdf_draws(title, subtitle):
            self._render_text_sdf(title, subtitle, title_anim, t, progress)
        else:
            self._render_text_pil(title, subtitle, title_anim, t, progress)

    def _sdf_draws(self, title: str, subtitle: str | None) -> bool:
        """Whether the SDF atlas has every letter; the PIL layers draw anything else."""
        return self._sdf_atlas is not None and self._sdf_atlas.draws(f"{title} {subtitle or ''}")

    def _base_sizes(self, subtitle: str | None) -> tuple[int, int]:
        """Title and subtitle font sizes before the layout gets a say.

        WHY: base size on min(w,h) so portrait text isn't giant.
        Same approach as map titles (rendering_service.py:180).
        """
        cfg = self.config
        base = min(cfg.width, cfg.height)
        ratio = cfg.title_size_ratio * 0.65 if subtitle else cfg.title_size_ratio
        return int(base * ratio), int(base * cfg.subtitle_size_ratio)

    def _stack(
        self,
        title: str,
        subtitle: str | None,
        count_lines: Callable[[str, int], int],
        sizes: tuple[int, int] | None = None,
    ) -> TextStack:
        """Stack the two blocks for the line counts this drawing path produces."""
        title_size, subtitle_size = sizes or self._base_sizes(subtitle)
        return stack_text_blocks(
            title,
            subtitle,
            title_size,
            subtitle_size,
            frame_height=self.config.height,
            count_lines=count_lines,
            gap_ratio=self.config.title_subtitle_gap_ratio,
        )

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
        t: float,
        progress: float,
    ):
        """Render text using GPU SDF kernels."""
        cfg = self.config
        stack = self._stack(title, subtitle, _single_line)
        title_y = title_anim["y_offset"] + stack.title_shift
        if cfg.enable_shadow:
            self._render_sdf_text_direct(
                title,
                stack.title_size,
                (0.0, 0.0, 0.0),
                title_anim["opacity"] * cfg.shadow_opacity,
                y_offset=title_y,
                x_offset=title_anim["x_offset"],
                is_shadow=True,
            )
        self._render_sdf_text_direct(
            title,
            stack.title_size,
            self.text_rgb,
            title_anim["opacity"],
            y_offset=title_y,
            x_offset=title_anim["x_offset"],
        )
        if subtitle:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            self._render_sdf_text_direct(
                subtitle,
                stack.subtitle_size,
                self.text_rgb,
                subtitle_anim["opacity"],
                y_offset=subtitle_anim["y_offset"] + stack.subtitle_shift,
                x_offset=subtitle_anim["x_offset"],
            )

    def _render_text_pil(
        self,
        title: str,
        subtitle: str | None,
        title_anim: dict,
        t: float,
        progress: float,
    ):
        """Render text using PIL-based layers (GPU-cached)."""
        cfg = self.config
        stack = self._render_text_layers(title, subtitle)

        if cfg.enable_shadow and self._shadow_layer_gpu is not None:
            shadow_offset = max(2, int(cfg.height * cfg.shadow_offset_ratio))
            self._composite(
                self._shadow_layer_gpu,
                title_anim["opacity"] * cfg.shadow_opacity,
                title_anim["y_offset"] + stack.title_shift + shadow_offset,
                title_anim["x_offset"] + shadow_offset,
            )

        if self._title_layer_gpu is not None:
            self._composite(
                self._title_layer_gpu,
                title_anim["opacity"],
                title_anim["y_offset"] + stack.title_shift,
                title_anim["x_offset"],
            )

        if self._subtitle_layer_gpu is not None:
            subtitle_anim = self._compute_animation(t, progress, is_subtitle=True)
            self._composite(
                self._subtitle_layer_gpu,
                subtitle_anim["opacity"],
                subtitle_anim["y_offset"] + stack.subtitle_shift,
                subtitle_anim["x_offset"],
            )

    def _composite(self, layer: Any, opacity: float, y_offset: float, x_offset: float) -> None:
        kernels._composite_text_with_offset(
            self.gpu.frame, layer, self.gpu.temp, opacity, y_offset, x_offset
        )
        kernels._copy_field_3(self.gpu.temp, self.gpu.frame)

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
        if not self.use_sdf or self._sdf_atlas is None or kernels._render_sdf_text is None:
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
        kernels._render_sdf_text(
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

    def _font(self, font_size: int, text: str):
        """The configured face at this size, or Pillow's own when it is missing.

        The fallback is asked for a size because the layout is measured in
        pixels: a face that ignores the size would place text nowhere near
        where the stack expects it. A face without one of the text's letters
        gives way to one that has them all (#1101).
        """
        # WHY bold: _get_system_font takes the heaviest weight the family has.
        path = font_covering(_get_system_font(self.config.font_family), text, bold=True)
        try:
            return ImageFont.truetype(path, font_size)
        except (OSError, ValueError):
            return ImageFont.load_default(size=font_size)

    def _pil_line_counter(self) -> Callable[[str, int], int]:
        """How many lines a string wraps to, measured the way the layers draw it."""
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        safe_width = safe_text_width(self.config.width, self.config.height)

        def count(text: str, font_size: int) -> int:
            return len(
                _split_text_for_rendering(draw, text, self._font(font_size, text), safe_width)
            )

        return count

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
        font = self._font(font_size, text)

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

    def plan_text_layers(self, title: str, subtitle: str | None) -> TextPlan:
        """Rasterize both text layers, placed so that they cannot share a row.

        The stack comes from the wrapped line counts, and the rendered layers
        are then put to `text_blocks_overlap`: a face that draws taller than the
        line height it was measured at gets one warning and a smaller plan,
        never a silent collision.
        """
        count_lines = self._pil_line_counter()
        stack = self._stack(title, subtitle, count_lines)
        plan = self._raster(title, subtitle, stack)
        for _ in range(_OVERLAP_ATTEMPTS):
            if not text_blocks_overlap(
                plan.title_layer, plan.subtitle_layer, stack.title_shift, stack.subtitle_shift
            ):
                return plan
            logger.warning(
                "Title %r and its subtitle share ink at %dpx; shrinking both",
                title,
                stack.title_size,
            )
            smaller = shrink_sizes(stack.title_size, stack.subtitle_size, self.config.height)
            if smaller is None:
                return plan
            stack = self._stack(title, subtitle, count_lines, smaller)
            plan = self._raster(title, subtitle, stack)
        return plan

    def _raster(self, title: str, subtitle: str | None, stack: TextStack) -> TextPlan:
        """Draw the layers this stack asks for, at its sizes."""
        tr, tg, tb = self.text_rgb
        text_rgba = (int(tr * 255), int(tg * 255), int(tb * 255), 255)
        shadow_rgba = (0, 0, 0, int(self.config.shadow_opacity * 255))
        return TextPlan(
            stack=stack,
            title_layer=self._render_text_layer(title, stack.title_size, text_rgba),
            subtitle_layer=(
                self._render_text_layer(subtitle, stack.subtitle_size, text_rgba)
                if subtitle
                else None
            ),
            shadow_layer=(
                self._render_text_layer(title, stack.title_size, shadow_rgba)
                if self.config.enable_shadow
                else None
            ),
        )

    def _render_text_layers(self, title: str, subtitle: str | None) -> TextStack:
        """Pre-render text layers (cached on GPU) and say where they go."""
        if self._cached_text == (title, subtitle) and self._text_stack is not None:
            return self._text_stack

        plan = self.plan_text_layers(title, subtitle)
        self._title_layer_gpu = self._upload(plan.title_layer)
        self._shadow_layer_gpu = self._upload(plan.shadow_layer)
        self._subtitle_layer_gpu = self._upload(plan.subtitle_layer)

        self._text_stack = plan.stack
        self._cached_text = (title, subtitle)
        return plan.stack

    @staticmethod
    def _upload(layer: np.ndarray | None):
        """Hand a rendered layer to the device, once, for every frame to reuse."""
        if layer is None:
            return None
        buffer = ti.ndarray(dtype=ti.f32, shape=layer.shape)
        buffer.from_numpy(layer)
        return buffer
