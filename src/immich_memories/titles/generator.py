"""Main title screen generation logic.

This module orchestrates the creation of:
- Opening title screens with animated backgrounds
- Month divider screens for section breaks
- Ending screens with dominant color fade

It integrates text generation, styling, and rendering to produce
complete title screen video clips ready for assembly.

Key Classes:
    TitleScreenConfig: Configuration for title generation (resolution, timing, style)
    TitleScreenGenerator: Main generator class that produces all screen types
    GeneratedScreen: Result object containing path, duration, and screen type

Rendering:
    The module automatically uses GPU-accelerated Taichi rendering when available,
    falling back to PIL-based rendering otherwise. GPU rendering is 15-60x faster.

Audio:
    All generated screens include a silent audio track for FFmpeg concat
    compatibility. This prevents "no audio stream" errors during assembly.

Safe Margins:
    Text is automatically scaled to fit within 80% of screen width (10% margin
    each side) to prevent overflow on any orientation.

Usage:
    ```python
    from immich_memories.titles.generator import (
        TitleScreenGenerator,
        TitleScreenConfig,
        generate_title_screen,
    )

    # Quick function
    path = generate_title_screen(
        title="2024",
        subtitle="Family Memories",
        orientation="landscape",
        resolution="1080p",
    )

    # Or use the generator class
    config = TitleScreenConfig(orientation="portrait", resolution="4k")
    generator = TitleScreenGenerator(config=config)
    screens = generator.generate_all_screens(
        year=2024,
        person_name="Emma",
        video_clips=[clip1, clip2],
    )
    ```
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .colors import extract_dominant_color
from .renderer_pil import create_title_video
from .styles import TitleStyle, get_random_style, get_style_for_mood

# Try to import GPU-accelerated renderer
try:
    from .renderer_taichi import (
        TaichiTitleConfig,
        create_title_video_taichi,
        init_taichi,
        is_taichi_available,
    )
    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False
    create_title_video_taichi = None
    TaichiTitleConfig = None
    init_taichi = None
    is_taichi_available = None
from .text_builder import (
    SelectionType,
    generate_month_divider_text,
    generate_title,
    infer_selection_type,
)

logger = logging.getLogger(__name__)


def _get_gpu_encoder_args() -> list[str]:
    """Get GPU-accelerated encoder arguments with 10-bit HDR (HLG) support.

    Title screens must match the colorspace of video clips (bt2020/HLG)
    to avoid decoder artifacts when concatenated with stream copy.
    """
    import subprocess
    import sys

    # HLG colorspace metadata — must match _encode_single_clip in assembly.py
    color_args = [
        "-color_primaries", "bt2020",
        "-color_trc", "arib-std-b67",
        "-colorspace", "bt2020nc",
    ]

    # macOS: VideoToolbox (GPU accelerated)
    if sys.platform == "darwin":
        return [
            "-c:v", "hevc_videotoolbox",
            "-q:v", "50",
            "-pix_fmt", "p010le",  # 10-bit
            "-tag:v", "hvc1",
            *color_args,
        ]

    # Check for NVIDIA NVENC
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True,
        )
        if "hevc_nvenc" in result.stdout:
            return [
                "-c:v", "hevc_nvenc",
                "-preset", "p4",
                "-rc", "constqp", "-qp", "18",
                "-pix_fmt", "p010le",  # 10-bit
                "-tag:v", "hvc1",
                *color_args,
            ]
    except Exception:
        pass

    # Fallback to CPU libx265 (slower)
    return [
        "-c:v", "libx265",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p10le",
        "-tag:v", "hvc1",
        *color_args,
    ]


# Standard resolutions for each orientation
ORIENTATION_RESOLUTIONS: dict[str, dict[str, tuple[int, int]]] = {
    "landscape": {
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
    },
    "portrait": {
        "720p": (720, 1280),
        "1080p": (1080, 1920),
        "4k": (2160, 3840),
    },
    "square": {
        "720p": (720, 720),
        "1080p": (1080, 1080),
        "4k": (2160, 2160),
    },
}


def get_resolution_for_orientation(
    orientation: str,
    resolution: str = "1080p",
) -> tuple[int, int]:
    """Get the appropriate resolution for an orientation.

    Args:
        orientation: One of "landscape", "portrait", "square".
        resolution: One of "720p", "1080p", "4k".

    Returns:
        Tuple of (width, height) for the given orientation and resolution.
    """
    if orientation not in ORIENTATION_RESOLUTIONS:
        orientation = "landscape"
    if resolution not in ORIENTATION_RESOLUTIONS[orientation]:
        resolution = "1080p"
    return ORIENTATION_RESOLUTIONS[orientation][resolution]


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

    # Month dividers
    show_month_dividers: bool = True
    month_divider_threshold: int = 2  # Minimum clips to show month divider

    # Output orientation and resolution
    orientation: str = "landscape"  # "landscape", "portrait", or "square"
    resolution: str = "1080p"  # "720p", "1080p", or "4k"
    fps: float = 60.0  # 60fps for smooth animations (downsample later if needed)

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

    This is the main entry point for generating all title-related video clips.
    It handles:
    - Title text generation (localized)
    - Style selection (mood-based or random)
    - Rendering (GPU-accelerated when available)
    - Audio track inclusion (silent, for FFmpeg compatibility)

    The generator automatically selects the best renderer:
    1. Taichi GPU renderer (if installed and GPU available)
    2. PIL renderer (fallback)

    All generated videos include silent audio tracks to ensure FFmpeg
    concat operations work correctly during assembly.

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
        """Initialize the generator.

        Args:
            config: Configuration for generation. Uses defaults if None.
            style: Visual style. If None, determined from mood or randomly.
            mood: Video mood for automatic style selection (e.g., "happy", "calm").
            output_dir: Directory for generated videos. Defaults to ./title_screens.
        """
        self.config = config or TitleScreenConfig()
        self.output_dir = output_dir or Path.cwd() / "title_screens"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._mood = mood  # Store mood for logging

        # Determine style
        if style is not None:
            self.style = style
            logger.info(f"Using provided style: {self.style.name}")
        elif self.config.style_mode == "auto" and mood:
            self.style = get_style_for_mood(mood)
            logger.info(f"Using mood-based style: {self.style.name} (mood: {mood})")
        elif self.config.style_mode == "random":
            self.style = get_random_style()
            logger.info(f"Using random style: {self.style.name}")
        elif self.config.style_mode not in ("auto", "random"):
            # Try to load specific style by name
            from .styles import PRESET_STYLES
            self.style = PRESET_STYLES.get(self.config.style_mode, get_random_style())
            logger.info(f"Using named style: {self.style.name}")
        else:
            self.style = get_random_style()
            logger.info(f"Using default random style: {self.style.name}")

        # Apply decorative line preference
        if not self.config.show_decorative_lines:
            self.style.use_line_accent = False

        # Initialize GPU renderer if requested and available
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

        Args:
            fade_from_white: If True, fade from white at start (for intro title only).
            is_birthday: If True, enable festive birthday particle effects.
        """
        if self._use_gpu and create_title_video_taichi is not None:
            # Map style to TaichiTitleConfig
            # Map background_type: soft_gradient/vignette use linear, solid uses radial
            gradient_type = "linear" if style.background_type != "radial" else "radial"

            config = TaichiTitleConfig(
                width=width,
                height=height,
                fps=fps,
                duration=duration,
                # Background colors from style
                bg_color1=style.background_colors[0] if style.background_colors else "#FFF5E6",
                bg_color2=style.background_colors[1] if len(style.background_colors) > 1 else style.background_colors[0] if style.background_colors else "#FFE4CC",
                gradient_angle=float(style.background_angle),
                gradient_type=gradient_type,
                # Text styling
                text_color=style.text_color,
                title_size_ratio=style.title_size_ratio,
                subtitle_size_ratio=style.subtitle_size_ratio * style.title_size_ratio,  # Convert relative to absolute
                # Effects
                enable_bokeh=True,  # Bokeh looks good
                enable_shadow=getattr(style, 'text_shadow', False),
                # Animated background
                gradient_rotation=10.0 if animated_background else 0.0,
                color_pulse_amount=0.03 if animated_background else 0.0,
                vignette_pulse=0.05 if animated_background else 0.0,
                # Birthday celebration effects
                is_birthday=is_birthday,
            )
            return create_title_video_taichi(title, subtitle, output_path, config, fade_from_white)
        else:
            # Use PIL renderer
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
            )

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
        """Generate the opening title screen.

        Args:
            year: Year for title.
            month: Month for title.
            start_date: Start date for range.
            end_date: End date for range.
            person_name: Person's name (subtitle).
            birthday_age: Age for birthday titles.
            selection_type: Type of selection (auto-detected if not provided).

        Returns:
            GeneratedScreen with path to video file.
        """
        # Infer selection type if not provided
        if selection_type is None:
            selection_type = infer_selection_type(
                year=year,
                month=month,
                start_date=start_date,
                end_date=end_date,
                birthday_age=birthday_age,
            )

        # Log input parameters
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

        # Generate title text
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

        # Log generated text
        logger.info("-" * 40)
        logger.info(f"  Main title: \"{title_info.main_title}\"")
        if title_info.subtitle:
            logger.info(f"  Subtitle: \"{title_info.subtitle}\"")
        else:
            logger.info("  Subtitle: (none)")

        # Log style info
        logger.info("-" * 40)
        logger.info(f"  Style: {self.style.name}")
        logger.info(f"  Font: {self.style.font_family} ({self.style.font_weight})")
        logger.info(f"  Animation: {self.style.animation_preset}")
        logger.info(f"  Background: {self.style.background_type}")
        if self._mood:
            logger.info(f"  Mood: {self._mood}")

        # Log output settings
        width, height = self.config.output_resolution
        logger.info("-" * 40)
        logger.info(f"  Resolution: {width}x{height}")
        logger.info(f"  FPS: {self.config.fps}")
        logger.info(f"  Duration: {self.config.title_duration}s")
        logger.info(f"  Animated background: {self.config.animated_background}")
        logger.info("=" * 60)

        # Generate video
        output_path = self.output_dir / "title_screen.mp4"

        self._create_title_video(
            title=title_info.main_title,
            subtitle=title_info.subtitle,
            style=self.style,
            output_path=output_path,
            width=width,
            height=height,
            duration=self.config.title_duration,
            fps=self.config.fps,
            animated_background=self.config.animated_background,
            fade_from_white=True,  # Intro title fades FROM white
        )

        renderer_type = "GPU (Taichi)" if self._use_gpu else "CPU (PIL)"
        logger.info(f"Title screen generated [{renderer_type}]: {output_path}")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.title_duration,
            screen_type="title",
        )

    def generate_month_divider(
        self,
        month: int,
        year: int | None = None,
        is_birthday_month: bool = False,
    ) -> GeneratedScreen:
        """Generate a month divider screen.

        Args:
            month: Month number (1-12).
            year: Optional year to include.
            is_birthday_month: If True, add birthday celebration elements.

        Returns:
            GeneratedScreen with path to video file.
        """
        # Generate month text
        month_text = generate_month_divider_text(
            month,
            year=year,
            locale=self.config.locale,
        )

        # Add birthday celebration elements
        subtitle = None
        animation_preset = "slow_fade"
        if is_birthday_month:
            # Add festive subtitle with emojis
            subtitle = "🎂 🎈 🎉"
            animation_preset = "bounce_in"  # More energetic animation for birthday
            logger.info(f"Adding birthday celebration to month divider: {month_text}")

        # Use simpler style for month dividers
        divider_style = TitleStyle(
            name=f"{self.style.name}_divider",
            font_family=self.style.font_family,
            font_weight="light",  # Lighter weight for dividers
            title_size_ratio=0.08,  # Smaller than main title
            text_color=self.style.text_color,
            background_type=self.style.background_type,
            background_colors=self.style.background_colors,
            animation_preset=animation_preset,
            use_line_accent=False,  # No decorative lines
        )

        output_path = self.output_dir / f"month_divider_{month:02d}.mp4"

        width, height = self.config.output_resolution

        self._create_title_video(
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

    def generate_ending_screen(
        self,
        video_clips: list[Path] | None = None,
        dominant_color: tuple[int, int, int] | None = None,
    ) -> GeneratedScreen:
        """Generate the ending screen with fade to white.

        Args:
            video_clips: List of video clip paths (unused, kept for API compatibility).
            dominant_color: Ignored - always fades to white.

        Returns:
            GeneratedScreen with path to video file.
        """
        # Always fade to white for clean ending
        fade_to_color = (255, 255, 255)

        # Create ending screen with fade
        output_path = self.output_dir / "ending_screen.mp4"

        width, height = self.config.output_resolution

        self._create_ending_video(
            output_path=output_path,
            fade_to_color=fade_to_color,
            width=width,
            height=height,
            duration=self.config.ending_duration,
            fps=self.config.fps,
        )

        logger.info("Generated ending screen with fade to white")

        return GeneratedScreen(
            path=output_path,
            duration=self.config.ending_duration,
            screen_type="ending",
        )

    def _create_ending_video(
        self,
        output_path: Path,
        fade_to_color: tuple[int, int, int],
        width: int,
        height: int,
        duration: float,
        fps: float,
    ) -> None:
        """Create ending video with fade to specified color.

        Memory-optimized: streams frames directly to FFmpeg instead of
        saving to disk. This reduces memory usage from ~2-3GB to ~100MB
        for 4K video.

        Args:
            output_path: Output video path.
            fade_to_color: Color to fade to (typically white).
            width: Video width.
            height: Video height.
            duration: Video duration.
            fps: Frames per second.
        """
        import subprocess

        try:
            from PIL import Image
        except ImportError:
            raise ImportError("PIL/Pillow is required for ending screen generation")

        from .backgrounds import create_background_for_style

        total_frames = int(duration * fps)
        fade_start_frame = int(1.5 * fps)  # Start fade at 1.5s
        fade_duration_frames = total_frames - fade_start_frame

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build FFmpeg command to read raw RGB frames from stdin
        encoder_args = _get_gpu_encoder_args()
        cmd = [
            "ffmpeg",
            "-y",
            # Input: raw RGB frames from stdin
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", "pipe:0",
            # Add silent audio track (required for assembly compatibility)
            "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
            # Video encoding - GPU accelerated with 10-bit for smooth gradients
            *encoder_args,
            # Audio encoding
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ]

        # Create base background (same as title but no text)
        base_bg = create_background_for_style(
            width,
            height,
            self.style.background_type,
            self.style.background_colors,
            self.style.background_angle,
        )

        # Ensure RGB mode for raw video encoding
        if base_bg.mode != "RGB":
            base_bg = base_bg.convert("RGB")

        # Create solid color frame for fade target
        solid_color = Image.new("RGB", (width, height), fade_to_color)

        # Cache base background bytes (reused for non-fade frames)
        base_bg_bytes = base_bg.tobytes()

        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            for i in range(total_frames):
                if i < fade_start_frame:
                    # Before fade - reuse cached base background bytes
                    process.stdin.write(base_bg_bytes)
                else:
                    # During fade - blend and write immediately
                    fade_progress = (i - fade_start_frame) / fade_duration_frames
                    fade_progress = min(1.0, fade_progress)

                    # Use smooth easing
                    fade_progress = fade_progress ** 0.5  # Ease out

                    # Blend images
                    frame = Image.blend(base_bg, solid_color, fade_progress)
                    process.stdin.write(frame.tobytes())
                    del frame  # Immediate cleanup

            process.stdin.close()
            _, stderr = process.communicate()

            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()[-500:]}")

        except BrokenPipeError:
            _, stderr = process.communicate()
            raise RuntimeError(f"FFmpeg pipe broken: {stderr.decode()[-500:]}")

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
        """Generate all screens needed for a video.

        Args:
            year: Year for title.
            month: Month for title.
            start_date: Start date for range.
            end_date: End date for range.
            person_name: Person's name.
            birthday_age: Age for birthday titles.
            video_clips: List of video clips for color extraction.
            months_in_video: List of months present in video (for month dividers).

        Returns:
            Dict mapping screen type to GeneratedScreen.
        """
        screens: dict[str, GeneratedScreen] = {}

        # Generate title screen
        screens["title"] = self.generate_title_screen(
            year=year,
            month=month,
            start_date=start_date,
            end_date=end_date,
            person_name=person_name,
            birthday_age=birthday_age,
        )

        # Generate month dividers if needed
        if (
            self.config.show_month_dividers
            and months_in_video
            and len(months_in_video) > 1
        ):
            for m in months_in_video:
                screens[f"month_{m:02d}"] = self.generate_month_divider(m, year=year)

        # Generate ending screen
        screens["ending"] = self.generate_ending_screen(video_clips=video_clips)

        return screens


# Convenience functions

def generate_title_screen(
    title: str,
    subtitle: str | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 3.5,
    fps: float = 60.0,
    animated_background: bool = True,
) -> Path:
    """Generate a title screen video.

    Convenience function for simple title screen generation.

    Args:
        title: Main title text.
        subtitle: Optional subtitle.
        style: Visual style (random if not provided).
        output_path: Output path (auto-generated if not provided).
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).
        animated_background: Enable animated background effects.

    Returns:
        Path to generated video file.
    """
    if style is None:
        style = get_random_style()

    if output_path is None:
        output_path = Path.cwd() / "title_screen.mp4"

    width, height = get_resolution_for_orientation(orientation, resolution)

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
    )


def generate_month_divider(
    month: int,
    year: int | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    locale: str = "en",
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 2.0,
    fps: float = 60.0,
    animated_background: bool = True,
) -> Path:
    """Generate a month divider video.

    Args:
        month: Month number (1-12).
        year: Optional year.
        style: Visual style.
        output_path: Output path.
        locale: Language code.
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).
        animated_background: Enable animated background effects.

    Returns:
        Path to generated video file.
    """
    if style is None:
        style = get_random_style()

    month_text = generate_month_divider_text(month, year, locale)

    if output_path is None:
        output_path = Path.cwd() / f"month_divider_{month:02d}.mp4"

    # Use simpler style for dividers
    divider_style = TitleStyle(
        name=f"{style.name}_divider",
        font_family=style.font_family,
        font_weight="light",
        title_size_ratio=0.08,
        text_color=style.text_color,
        background_type=style.background_type,
        background_colors=style.background_colors,
        animation_preset="slow_fade",
        use_line_accent=False,
    )

    width, height = get_resolution_for_orientation(orientation, resolution)

    return create_title_video(
        title=month_text,
        subtitle=None,
        style=divider_style,
        output_path=output_path,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        animated_background=animated_background,
    )


def generate_ending_screen(
    video_clips: list[Path] | None = None,
    dominant_color: tuple[int, int, int] | None = None,
    style: TitleStyle | None = None,
    output_path: Path | None = None,
    orientation: str = "landscape",
    resolution: str = "1080p",
    duration: float = 4.0,
    fps: float = 60.0,
) -> Path:
    """Generate an ending screen with fade to white.

    Args:
        video_clips: Unused, kept for API compatibility.
        dominant_color: Unused, always fades to white.
        style: Visual style for background.
        output_path: Output path.
        orientation: Video orientation ("landscape", "portrait", "square").
        resolution: Video resolution ("720p", "1080p", "4k").
        duration: Duration in seconds.
        fps: Frames per second (default 60 for smooth animations).

    Returns:
        Path to generated video file.
    """
    if style is None:
        style = get_random_style()

    if output_path is None:
        output_path = Path.cwd() / "ending_screen.mp4"

    config = TitleScreenConfig(
        ending_duration=duration,
        orientation=orientation,
        resolution=resolution,
        fps=fps,
    )

    generator = TitleScreenGenerator(config=config, style=style)
    result = generator.generate_ending_screen()

    return result.path
