"""Title screens, month dividers, and ending screens for video compilations.

This module provides:
- Dynamic title generation based on date ranges and selection type
- Professional visual styling with mood-based customization
- Subtle text animations with proper easing
- Gradient backgrounds and decorative elements
- Dominant color extraction for ending screens
- Full localization support (EN, FR, extensible)
"""

from __future__ import annotations

from .animations import (
    EASING_FUNCTIONS,
    TEXT_ANIMATIONS,
    AnimationPreset,
    EasingFunction,
    apply_easing,
    reverse_preset,
)
from .backgrounds import (
    AnimatedBackgroundConfig,
    BackgroundType,
    create_animated_background,
    create_gradient_background,
    create_radial_gradient,
    create_vignette_background,
)
from .colors import (
    brighten_color,
    ensure_minimum_brightness,
    extract_dominant_color,
    hex_to_rgb,
    rgb_to_hex,
)
from .fonts import (
    FONT_DEFINITIONS,
    FontManager,
    download_all_fonts,
    download_font,
    ensure_font_available,
    get_available_fonts,
    get_font_path,
    get_fonts_cache_dir,
    is_font_cached,
)
from .generator import (
    ORIENTATION_RESOLUTIONS,
    GeneratedScreen,
    TitleScreenConfig,
    TitleScreenGenerator,
    generate_ending_screen,
    generate_month_divider,
    generate_title_screen,
    get_resolution_for_orientation,
)
from .renderer_pil import (
    RenderSettings,
    TitleRenderer,
    create_title_video,
    render_title_frame,
)
from .styles import (
    COLOR_PALETTES,
    FONT_STACK,
    MOOD_STYLE_PROFILES,
    TitleStyle,
    get_random_style,
    get_style_for_mood,
)
from .text_builder import (
    SelectionType,
    TitleInfo,
    generate_title,
    get_month_name,
    get_ordinal,
)

__all__ = [
    # Styles
    "TitleStyle",
    "COLOR_PALETTES",
    "FONT_STACK",
    "MOOD_STYLE_PROFILES",
    "get_style_for_mood",
    "get_random_style",
    # Animations
    "AnimationPreset",
    "EasingFunction",
    "TEXT_ANIMATIONS",
    "EASING_FUNCTIONS",
    "apply_easing",
    "reverse_preset",
    # Text building
    "TitleInfo",
    "SelectionType",
    "generate_title",
    "get_month_name",
    "get_ordinal",
    # Backgrounds
    "BackgroundType",
    "AnimatedBackgroundConfig",
    "create_gradient_background",
    "create_radial_gradient",
    "create_vignette_background",
    "create_animated_background",
    # Colors
    "extract_dominant_color",
    "brighten_color",
    "ensure_minimum_brightness",
    "hex_to_rgb",
    "rgb_to_hex",
    # Rendering
    "TitleRenderer",
    "RenderSettings",
    "render_title_frame",
    "create_title_video",
    # Generation
    "TitleScreenConfig",
    "TitleScreenGenerator",
    "GeneratedScreen",
    "generate_title_screen",
    "generate_month_divider",
    "generate_ending_screen",
    # Orientation
    "get_resolution_for_orientation",
    "ORIENTATION_RESOLUTIONS",
    # Fonts
    "FontManager",
    "get_font_path",
    "get_fonts_cache_dir",
    "download_font",
    "download_all_fonts",
    "is_font_cached",
    "ensure_font_available",
    "get_available_fonts",
    "FONT_DEFINITIONS",
]
