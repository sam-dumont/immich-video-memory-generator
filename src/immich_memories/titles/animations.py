"""Animation definitions and easing functions for title screens.

Design principles:
- Duration: 300-600ms for text animations
- Easing: Natural curves (ease-out, ease-in-out)
- Motion: Subtle movement (10-30 pixels max displacement)
- NO: Typewriter, bouncing letters, spinning, aggressive scaling
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

# Type alias for easing functions
EasingFunction = Callable[[float], float]


# Standard easing functions
def linear(t: float) -> float:
    """Linear interpolation (no easing)."""
    return t


def ease_in_quad(t: float) -> float:
    """Quadratic ease-in."""
    return t * t


def ease_out_quad(t: float) -> float:
    """Quadratic ease-out."""
    return 1 - (1 - t) ** 2


def ease_in_out_quad(t: float) -> float:
    """Quadratic ease-in-out."""
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def ease_in_cubic(t: float) -> float:
    """Cubic ease-in."""
    return t ** 3


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out (recommended for most animations)."""
    return 1 - (1 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in-out."""
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_in_sine(t: float) -> float:
    """Sinusoidal ease-in."""
    return 1 - math.cos(t * math.pi / 2)


def ease_out_sine(t: float) -> float:
    """Sinusoidal ease-out."""
    return math.sin(t * math.pi / 2)


def ease_in_out_sine(t: float) -> float:
    """Sinusoidal ease-in-out (very smooth)."""
    return -(math.cos(math.pi * t) - 1) / 2


def ease_out_back(t: float) -> float:
    """Ease-out with slight overshoot (use sparingly)."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * ((t - 1) ** 3) + c1 * ((t - 1) ** 2)


def ease_out_expo(t: float) -> float:
    """Exponential ease-out (fast start, slow end)."""
    return 1 if t == 1 else 1 - 2 ** (-10 * t)


# Easing functions registry
EASING_FUNCTIONS: dict[str, EasingFunction] = {
    "linear": linear,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_in_cubic": ease_in_cubic,
    "ease_out_cubic": ease_out_cubic,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_in_sine": ease_in_sine,
    "ease_out_sine": ease_out_sine,
    "ease_in_out_sine": ease_in_out_sine,
    "ease_out_back": ease_out_back,
    "ease_out_expo": ease_out_expo,
}


# FFmpeg expression equivalents for drawtext filter
FFMPEG_EASING_EXPRESSIONS: dict[str, str] = {
    "linear": "{t}",
    "ease_out_quad": "1-pow(1-{t},2)",
    "ease_out_cubic": "1-pow(1-{t},3)",
    "ease_in_out_sine": "(1-cos(PI*{t}))/2",
    "ease_out_sine": "sin({t}*PI/2)",
    "ease_out_expo": "if(eq({t},1),1,1-pow(2,-10*{t}))",
}


@dataclass
class AnimationProperty:
    """A single animatable property."""

    from_value: float
    to_value: float

    def interpolate(self, progress: float) -> float:
        """Interpolate between from and to values."""
        return self.from_value + (self.to_value - self.from_value) * progress


@dataclass
class AnimationPreset:
    """Definition of a text animation preset."""

    name: str
    description: str
    duration_ms: int = 500
    easing: str = "ease_out_cubic"
    stagger_ms: int = 0  # Delay between title and subtitle

    # Animatable properties
    opacity: AnimationProperty | None = None
    y_offset: AnimationProperty | None = None  # pixels
    x_offset: AnimationProperty | None = None  # pixels
    scale: AnimationProperty | None = None  # 0.0 to 1.0+
    blur: AnimationProperty | None = None  # pixels

    # Multi-phase animations (for complex sequences)
    phases: list[dict] = field(default_factory=list)

    def get_easing_function(self) -> EasingFunction:
        """Get the easing function for this animation."""
        return EASING_FUNCTIONS.get(self.easing, ease_out_cubic)

    def compute_values(self, progress: float) -> dict[str, float]:
        """Compute all animated values at a given progress (0.0 to 1.0).

        Args:
            progress: Animation progress from 0.0 (start) to 1.0 (end).

        Returns:
            Dict of property names to their interpolated values.
        """
        easing = self.get_easing_function()
        eased_progress = easing(progress)

        values = {}

        if self.opacity:
            values["opacity"] = self.opacity.interpolate(eased_progress)
        if self.y_offset:
            values["y_offset"] = self.y_offset.interpolate(eased_progress)
        if self.x_offset:
            values["x_offset"] = self.x_offset.interpolate(eased_progress)
        if self.scale:
            values["scale"] = self.scale.interpolate(eased_progress)
        if self.blur:
            values["blur"] = self.blur.interpolate(eased_progress)

        return values


def reverse_preset(preset: AnimationPreset) -> AnimationPreset:
    """Create a reversed version of an animation preset.

    This reverses all animation properties so that the animation plays backward.
    For example, if fade_up animates opacity 0→1 and y_offset 20→0,
    the reversed version animates opacity 1→0 and y_offset 0→20.

    Used for text fade-out at the end of title screens.

    Args:
        preset: The animation preset to reverse.

    Returns:
        A new AnimationPreset with reversed properties.
    """
    return AnimationPreset(
        name=f"{preset.name}_reverse",
        description=f"Reversed: {preset.description}",
        duration_ms=preset.duration_ms,
        easing=preset.easing,
        stagger_ms=preset.stagger_ms,
        opacity=AnimationProperty(
            from_value=preset.opacity.to_value,
            to_value=preset.opacity.from_value,
        ) if preset.opacity else None,
        y_offset=AnimationProperty(
            from_value=preset.y_offset.to_value,
            to_value=preset.y_offset.from_value,
        ) if preset.y_offset else None,
        x_offset=AnimationProperty(
            from_value=preset.x_offset.to_value,
            to_value=preset.x_offset.from_value,
        ) if preset.x_offset else None,
        scale=AnimationProperty(
            from_value=preset.scale.to_value,
            to_value=preset.scale.from_value,
        ) if preset.scale else None,
        blur=AnimationProperty(
            from_value=preset.blur.to_value,
            to_value=preset.blur.from_value,
        ) if preset.blur else None,
        phases=[],  # Don't reverse complex phase animations
    )


# Pre-defined animation presets
# Values increased for MORE VISIBLE motion (not subtle/static looking)
TEXT_ANIMATIONS: dict[str, AnimationPreset] = {
    "fade_up": AnimationPreset(
        name="fade_up",
        description="Text fades in while moving up noticeably",
        duration_ms=600,
        easing="ease_out_cubic",
        stagger_ms=120,
        opacity=AnimationProperty(from_value=0, to_value=1),
        y_offset=AnimationProperty(from_value=50, to_value=0),  # was 20
        scale=AnimationProperty(from_value=0.92, to_value=1.0),  # added subtle scale
    ),
    "fade_down": AnimationPreset(
        name="fade_down",
        description="Text fades in while moving down noticeably",
        duration_ms=600,
        easing="ease_out_cubic",
        stagger_ms=120,
        opacity=AnimationProperty(from_value=0, to_value=1),
        y_offset=AnimationProperty(from_value=-50, to_value=0),  # was -20
        scale=AnimationProperty(from_value=0.92, to_value=1.0),  # added subtle scale
    ),
    "gentle_scale": AnimationPreset(
        name="gentle_scale",
        description="Text fades in with visible scale increase",
        duration_ms=700,
        easing="ease_out_quad",
        stagger_ms=100,
        opacity=AnimationProperty(from_value=0, to_value=1),
        scale=AnimationProperty(from_value=0.80, to_value=1.0),  # was 0.95
    ),
    "slow_fade": AnimationPreset(
        name="slow_fade",
        description="Elegant fade with subtle scale",
        duration_ms=900,
        easing="ease_in_out_sine",
        stagger_ms=180,
        opacity=AnimationProperty(from_value=0, to_value=1),
        scale=AnimationProperty(from_value=0.95, to_value=1.0),  # added subtle scale
    ),
    "smooth_slide": AnimationPreset(
        name="smooth_slide",
        description="Text slides in from the right noticeably",
        duration_ms=600,
        easing="ease_out_cubic",
        stagger_ms=120,
        opacity=AnimationProperty(from_value=0, to_value=1),
        x_offset=AnimationProperty(from_value=80, to_value=0),  # was 30
        scale=AnimationProperty(from_value=0.95, to_value=1.0),  # added subtle scale
    ),
    "gentle_blur": AnimationPreset(
        name="gentle_blur",
        description="Text fades in from blur to sharp with scale",
        duration_ms=700,
        easing="ease_out_quad",
        stagger_ms=80,
        opacity=AnimationProperty(from_value=0, to_value=1),
        blur=AnimationProperty(from_value=15, to_value=0),  # was 8
        scale=AnimationProperty(from_value=0.90, to_value=1.0),  # added scale
    ),
    "line_reveal": AnimationPreset(
        name="line_reveal",
        description="Decorative line expands, then text fades in",
        duration_ms=500,
        easing="ease_out_quad",
        stagger_ms=250,
        opacity=AnimationProperty(from_value=0, to_value=1),
        scale=AnimationProperty(from_value=0.90, to_value=1.0),  # added scale
        phases=[
            {
                "element": "line",
                "properties": {"width": {"from": 0, "to": 80}},  # was 60
                "duration_ms": 400,
            },
            {
                "element": "text",
                "properties": {"opacity": {"from": 0, "to": 1}},
                "duration_ms": 500,
                "delay_ms": 200,
            },
        ],
    ),
    "scale_bounce": AnimationPreset(
        name="scale_bounce",
        description="Text scales up with visible overshoot",
        duration_ms=700,
        easing="ease_out_back",
        stagger_ms=100,
        opacity=AnimationProperty(from_value=0, to_value=1),
        scale=AnimationProperty(from_value=0.75, to_value=1.0),  # was 0.9
    ),
}


def apply_easing(t: float, easing_name: str = "ease_out_cubic") -> float:
    """Apply an easing function to a progress value.

    Args:
        t: Progress value from 0.0 to 1.0.
        easing_name: Name of the easing function.

    Returns:
        Eased progress value.
    """
    t = max(0.0, min(1.0, t))  # Clamp to 0-1
    easing_func = EASING_FUNCTIONS.get(easing_name, ease_out_cubic)
    return easing_func(t)


def get_animation_preset(name: str) -> AnimationPreset:
    """Get an animation preset by name.

    Args:
        name: Name of the animation preset.

    Returns:
        The animation preset, or default fade_up if not found.
    """
    return TEXT_ANIMATIONS.get(name, TEXT_ANIMATIONS["fade_up"])


def compute_frame_animation(
    preset: AnimationPreset,
    current_frame: int,
    fps: float,
    start_frame: int = 0,
) -> dict[str, float]:
    """Compute animation values for a specific frame.

    Args:
        preset: The animation preset to use.
        current_frame: Current frame number.
        fps: Frames per second.
        start_frame: Frame where animation starts.

    Returns:
        Dict of animated property values for this frame.
    """
    animation_frames = int(preset.duration_ms / 1000 * fps)
    elapsed_frames = current_frame - start_frame

    if elapsed_frames < 0:
        # Before animation starts
        progress = 0.0
    elif elapsed_frames >= animation_frames:
        # Animation complete
        progress = 1.0
    else:
        progress = elapsed_frames / animation_frames

    return preset.compute_values(progress)


def compute_staggered_animation(
    preset: AnimationPreset,
    current_frame: int,
    fps: float,
    element_index: int = 0,
    start_frame: int = 0,
) -> dict[str, float]:
    """Compute animation values with stagger for multiple elements.

    Args:
        preset: The animation preset to use.
        current_frame: Current frame number.
        fps: Frames per second.
        element_index: Index of the element (0 = title, 1 = subtitle).
        start_frame: Frame where first element's animation starts.

    Returns:
        Dict of animated property values for this frame and element.
    """
    stagger_frames = int(preset.stagger_ms / 1000 * fps)
    element_start = start_frame + (element_index * stagger_frames)

    return compute_frame_animation(preset, current_frame, fps, element_start)
