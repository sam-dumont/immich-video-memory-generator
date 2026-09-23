"""Rendering service for title screen video creation.

Provides renderer selection logic (GPU kernels vs CPU PIL) and
video creation methods for titles and map backgrounds.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer
from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

from .styles import TitleStyle
from .video_encoding import create_title_video

if TYPE_CHECKING:
    from .generator import TitleScreenConfig
    from .renderer_kernels import KernelTitleConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KernelRenderer:
    """The entry points the kernel renderer is used through."""

    create_video: Callable[..., Path]
    config_type: type[KernelTitleConfig]
    init_kernels: Callable[[], str | None]
    gpu_failures: Callable[[], tuple[str, ...]] = tuple


def load_kernel_renderer() -> KernelRenderer | None:
    """Import the kernel renderer, or None when this machine has to use PIL.

    WHY this is a function and not the import block that used to sit here: the
    import loads the kernel library's native runtime, and a processor without
    AVX dies inside that load with SIGILL, taking the whole interpreter with it
    (#910). A module-level import put that death between the CLI and its first
    line of output. The probe spends a child interpreter to find out first, and
    nothing is imported until the child has come back alive.
    """
    from .kernel_backend_probe import kernel_dispatch_failure

    if kernel_dispatch_failure() is not None:
        return None
    from .kernel_video import create_title_video_gpu
    from .kernels import gpu_startup_failures
    from .renderer_kernels import KernelTitleConfig, init_kernels

    return KernelRenderer(
        create_title_video_gpu, KernelTitleConfig, init_kernels, gpu_startup_failures
    )


# Kernel backends that are actually a GPU. "CPU" is a legitimate return from
# init_kernels(), so anything not listed here means the GPU renderer is
# running on the processor.
_GPU_BACKENDS = frozenset({"Metal", "CUDA", "Vulkan"})


class RenderingService:
    """Selects the GPU or PIL renderer and creates title/map videos."""

    def __init__(self, config: TitleScreenConfig) -> None:
        self.config = config
        self._use_gpu = False
        self.backend: str | None = None
        self._kernels: KernelRenderer | None = None
        if config.use_gpu_rendering:
            self._kernels = load_kernel_renderer()
            if self._kernels is not None:
                self.backend = self._kernels.init_kernels()
                self._use_gpu = self.backend is not None
            self._log_backend()

    def _log_backend(self) -> None:
        """Say what is really about to render, not what was hoped for.

        init_kernels() falls back to its CPU backend when Metal, CUDA and
        Vulkan all fail to start, and returns the string "CPU". The old line
        printed "GPU rendering enabled: CPU", so a container quietly rendering
        titles on the processor looked identical in the log to one using the
        card — and titles are the most expensive stage there is.
        """
        if self.backend is None:
            logger.warning("Title rendering: %s", self._no_backend_reason())
        elif self.backend in _GPU_BACKENDS:
            logger.info("Title rendering on GPU: %s", self.backend)
        else:
            failures = self._kernels.gpu_failures() if self._kernels is not None else ()
            logger.warning(
                "Title rendering on CPU: %s; the kernel renderer runs on %s instead. "
                "Titles will be markedly slower than the footage around them.",
                "; ".join(failures) or "the kernel library found no GPU backend",
                self.backend,
            )

    @staticmethod
    def _no_backend_reason() -> str:
        """Say which of the two ways to lose the kernels this machine took.

        "Kernel library unavailable" covered both, and on a CPU without AVX it
        was actively misleading: the library is installed and imports fine, it
        is the first kernel it compiles that the processor cannot execute.
        """
        from .kernel_backend_probe import kernel_dispatch_failure

        return kernel_dispatch_failure() or "the kernel library found no usable backend here"

    @property
    def use_gpu(self) -> bool:
        """Whether the GPU renderer is in use, not whether it has a GPU.

        Kept as-is because it selects the renderer, and the kernel path is the
        right choice even on CPU: it is the only one that can do the animated
        slow-mo deblur. Read `backend` to find out what is underneath.
        """
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
        frame_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Create title video using GPU or PIL renderer.

        The immutable encoding plan is read from the title config.
        """
        encoding_plan = self.config.encoding_plan
        if self._use_gpu and (kernels := self._kernels) is not None:
            return self._create_gpu_title(
                kernels,
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
                encoding_plan=encoding_plan,
                background_image=background_image,
                content_clip_path=content_clip_path,
                is_ending=is_ending,
                fade_to_white=fade_to_white,
                frame_progress=frame_progress,
            )
        # WHY: PIL fallback can't do animated slow-mo deblur, but it CAN
        # use a static blurred frame from the content clip as background
        # instead of falling back to a plain gradient.
        if background_image is None and content_clip_path is not None:
            background_image = self._extract_blurred_frame(
                content_clip_path,
                width,
                height,
                encoding_plan.target_transfer,
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
            background_image=background_image,
            encoding_plan=encoding_plan,
        )

    def _create_gpu_title(
        self,
        kernels: KernelRenderer,
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
        encoding_plan: EncodingPlan,
        background_image: np.ndarray | None = None,
        content_clip_path: Path | None = None,
        is_ending: bool = False,
        fade_to_white: bool = False,
        frame_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Create title video using the GPU-accelerated renderer."""
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
                source_transfer=encoding_plan.target_transfer,
            )
            if not slowmo_reader.is_active:
                slowmo_reader = None
                logger.info("Slowmo pipe failed, falling back to static frame")

        config = kernels.config_type(
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
            return kernels.create_video(
                title,
                subtitle,
                output_path,
                config,
                fade_from_white=fade_from_white,
                fade_to_white=fade_to_white,
                encoding_plan=encoding_plan,
                frame_progress=frame_progress,
                frame_transfer=(
                    encoding_plan.target_transfer if slowmo_reader is not None else HdrTransfer.NONE
                ),
            )
        finally:
            if slowmo_reader is not None:
                slowmo_reader.close()

    @staticmethod
    def _extract_blurred_frame(
        clip_path: Path,
        width: int,
        height: int,
        source_transfer: HdrTransfer = HdrTransfer.NONE,
    ) -> np.ndarray | None:
        """Extract a mid-clip frame in the SDR title working space.

        Falls back to None if extraction fails (caller uses gradient instead).
        """
        try:
            # The title pre-render is already positioned at the selected
            # content. Frame zero exists even for the 0.5-second ending source;
            # frame 30 does not at either 30 or 60 fps.
            filters: list[str] = []
            if source_transfer is not HdrTransfer.NONE:
                conversion = _get_hdr_conversion_filter(
                    source_transfer.value,
                    "sdr",
                    source_primaries="bt2020",
                    required=True,
                ).removeprefix(",")
                filters.append(conversion)
            filters.extend([f"scale={width}:{height}", "gblur=sigma=30"])

            # Extract mid-frame as raw RGB
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(clip_path),
                    "-vf",
                    ",".join(filters),
                    "-vframes",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None

            frame = np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)
            # Darken to 40% so white text pops (same as the GPU path)
            return frame.astype(np.float32) / 255.0 * 0.4
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.debug(f"Failed to extract blurred frame for PIL fallback: {e}")
            return None

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

        Uses the GPU kernels for text animation/encoding if available,
        falls back to PIL rendering with the map as static background.
        No bokeh/particles -- clean map aesthetic.
        """
        encoding_plan = self.config.encoding_plan
        if self._use_gpu and (kernels := self._kernels) is not None:
            # Dim the map so white text pops
            dimmed = background_array * 0.55
            # Target same absolute font size regardless of orientation
            # 0.09 of min(w,h), converted to height-relative ratio
            map_title_ratio = 0.135 * min(width, height) / height
            config = kernels.config_type(
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
            return kernels.create_video(
                title,
                subtitle,
                output_path,
                config,
                fade_from_white=True,
                encoding_plan=encoding_plan,
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
            encoding_plan=encoding_plan,
        )
