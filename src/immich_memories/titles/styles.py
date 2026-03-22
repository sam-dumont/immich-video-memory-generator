"""Visual style definitions for title screens.

This module contains:
- TitleStyle dataclass with all visual properties
- Color palettes (cinematic dark backgrounds with high-contrast white text)
- Font definitions with fallbacks
- Mood-to-style mapping for automatic selection
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class TitleStyle:
    """Complete visual style for title screens."""

    # Typography — Montserrat Bold matches the map titles for visual consistency
    font_family: str = "Montserrat"
    font_weight: Literal["light", "regular", "medium", "semibold"] = "semibold"
    title_size_ratio: float = 0.14  # Relative to screen height (0.08 - 0.15)
    subtitle_size_ratio: float = 0.6  # Relative to title size (0.5 - 0.7)
    letter_spacing: float = 0.02  # em units (-0.02 to 0.1)
    text_transform: Literal["none", "uppercase", "capitalize"] = "none"

    # Colors
    text_color: str = "#FFFFFF"
    text_shadow: bool = False
    text_blend_mode: Literal["normal", "multiply", "overlay", "soft_light"] = "normal"
    accent_color: str = "#F59E0B"

    # Background
    background_type: Literal["solid_gradient", "soft_gradient", "vignette", "content_backed"] = (
        "content_backed"
    )
    background_colors: list[str] = field(default_factory=lambda: ["#1A1A2E", "#16213E"])
    background_angle: int = 135  # degrees for linear gradient

    # Animation preset name (see animations.py)
    animation_preset: str = "fade_up"

    # Decorative elements
    use_line_accent: bool = False
    line_position: Literal["above", "below", "both", "none"] = "above"
    line_width: int = 60  # pixels
    line_thickness: int = 1  # pixels

    # Style name for identification
    name: str = "default"

    def get_font_path(self, fonts_dir: Path | None = None) -> Path | None:
        """Get the path to the font file.

        Args:
            fonts_dir: Directory containing bundled fonts. If None, uses module's fonts dir.

        Returns:
            Path to font file, or None if not found.
        """
        if fonts_dir is None:
            fonts_dir = Path(__file__).parent.parent / "fonts"

        weight_map = {
            "light": "Light",
            "regular": "Regular",
            "medium": "Medium",
            "semibold": "SemiBold",
        }

        font_filename = f"{self.font_family}-{weight_map[self.font_weight]}.ttf"
        font_path = fonts_dir / self.font_family / font_filename

        if font_path.exists():
            return font_path

        # Try alternate naming conventions
        alternates = [
            fonts_dir / self.font_family / f"{self.font_family}-{weight_map[self.font_weight]}.otf",
            fonts_dir / f"{self.font_family}-{weight_map[self.font_weight]}.ttf",
        ]

        for alt in alternates:
            if alt.exists():
                return alt

        return None


# Cinematic dark palettes — white text on dark backgrounds for professional look.
# Legacy pastel palettes preserved with _legacy_ prefix for backwards compatibility.
COLOR_PALETTES: dict[str, dict] = {
    "cinematic_dark": {
        "backgrounds": [
            ["#1A1A2E", "#16213E"],  # Deep navy
            ["#0F0F1A", "#1A1A2E"],  # Near black to navy
            ["#1C1917", "#292524"],  # Warm charcoal
        ],
        "text_colors": ["#FFFFFF", "#F5F5F4", "#E7E5E4"],
        "accents": ["#F59E0B", "#06B6D4", "#A855F7"],
    },
    "warm_dark": {
        "backgrounds": [
            ["#1C1917", "#292524"],  # Warm stone dark
            ["#27272A", "#3F3F46"],  # Zinc
            ["#1E1B18", "#3D3428"],  # Dark amber-tinted
        ],
        "text_colors": ["#FAFAF9", "#F5F5F4", "#E7E5E4"],
        "accents": ["#F59E0B", "#D97706", "#B45309"],
    },
    "deep_teal": {
        "backgrounds": [
            ["#042F2E", "#134E4A"],  # Deep teal
            ["#0C4A6E", "#0369A1"],  # Ocean blue
            ["#1E3A5F", "#2D5986"],  # Muted blue
        ],
        "text_colors": ["#FFFFFF", "#F0F9FF", "#E0F2FE"],
        "accents": ["#2DD4BF", "#06B6D4", "#22D3EE"],
    },
    "midnight": {
        "backgrounds": [
            ["#0F172A", "#1E293B"],  # Slate midnight
            ["#171717", "#262626"],  # Neutral dark
            ["#18181B", "#27272A"],  # Zinc dark
        ],
        "text_colors": ["#FFFFFF", "#F8FAFC", "#E2E8F0"],
        "accents": ["#818CF8", "#A78BFA", "#C084FC"],
    },
    # Legacy palettes — kept for backwards compat with explicit style_mode config
    "_legacy_warm_vibrant": {
        "backgrounds": [
            ["#FFF5E6", "#FFE4CC"],
            ["#FFF0F5", "#FFE4EC"],
            ["#FFFBEB", "#FEF3C7"],
        ],
        "text_colors": ["#2D2D2D", "#3D3D3D", "#4A4A4A"],
        "accents": ["#F59E0B", "#EC4899", "#8B5CF6"],
    },
    "_legacy_soft_pastels": {
        "backgrounds": [
            ["#F0F9FF", "#E0F2FE"],
            ["#FDF4FF", "#FAE8FF"],
            ["#F0FDF4", "#DCFCE7"],
        ],
        "text_colors": ["#374151", "#4B5563", "#6B7280"],
        "accents": ["#06B6D4", "#A855F7", "#10B981"],
    },
}


# Font stack with metadata
FONT_STACK: dict[str, dict] = {
    "Outfit": {
        "name": "Outfit",
        "weights": ["Light", "Regular", "Medium", "SemiBold"],
        "source": "Google Fonts",
        "license": "OFL",
        "style": "modern_geometric",
        "mood_affinity": ["happy", "energetic", "playful"],
    },
    "Raleway": {
        "name": "Raleway",
        "weights": ["Light", "Regular", "Medium", "SemiBold"],
        "source": "Google Fonts",
        "license": "OFL",
        "style": "elegant_minimal",
        "mood_affinity": ["calm", "nostalgic", "romantic"],
    },
    "JosefinSans": {
        "name": "Josefin Sans",
        "weights": ["Light", "Regular", "SemiBold"],
        "source": "Google Fonts",
        "license": "OFL",
        "style": "vintage_elegant",
        "mood_affinity": ["nostalgic", "romantic", "calm"],
    },
    "Quicksand": {
        "name": "Quicksand",
        "weights": ["Light", "Regular", "Medium", "SemiBold"],
        "source": "Google Fonts",
        "license": "OFL",
        "style": "friendly_rounded",
        "mood_affinity": ["happy", "playful", "energetic"],
    },
}


# Mood-to-style mapping — all moods use dark cinematic palettes
MOOD_STYLE_PROFILES: dict[str, dict] = {
    "happy": {
        "color_palette": "warm_dark",
        "preferred_fonts": ["Quicksand", "Outfit"],
        "animation_preset": "fade_up",
        "background_type": "content_backed",
        "font_weight": "semibold",
        "use_line_accent": False,
    },
    "calm": {
        "color_palette": "deep_teal",
        "preferred_fonts": ["Raleway", "Outfit"],
        "animation_preset": "slow_fade",
        "background_type": "content_backed",
        "font_weight": "medium",
        "use_line_accent": False,
    },
    "energetic": {
        "color_palette": "midnight",
        "preferred_fonts": ["Outfit", "Quicksand"],
        "animation_preset": "smooth_slide",
        "background_type": "content_backed",
        "font_weight": "semibold",
        "use_line_accent": False,
    },
    "nostalgic": {
        "color_palette": "warm_dark",
        "preferred_fonts": ["JosefinSans", "Raleway"],
        "animation_preset": "slow_fade",
        "background_type": "content_backed",
        "font_weight": "medium",
        "use_line_accent": False,
    },
    "romantic": {
        "color_palette": "warm_dark",
        "preferred_fonts": ["JosefinSans", "Raleway"],
        "animation_preset": "gentle_scale",
        "background_type": "content_backed",
        "font_weight": "medium",
        "use_line_accent": False,
    },
    "playful": {
        "color_palette": "midnight",
        "preferred_fonts": ["Quicksand", "Outfit"],
        "animation_preset": "fade_up",
        "background_type": "content_backed",
        "font_weight": "semibold",
        "use_line_accent": False,
    },
    "peaceful": {
        "color_palette": "deep_teal",
        "preferred_fonts": ["Raleway", "JosefinSans"],
        "animation_preset": "slow_fade",
        "background_type": "content_backed",
        "font_weight": "medium",
        "use_line_accent": False,
    },
    "exciting": {
        "color_palette": "cinematic_dark",
        "preferred_fonts": ["Outfit", "Quicksand"],
        "animation_preset": "smooth_slide",
        "background_type": "content_backed",
        "font_weight": "semibold",
        "use_line_accent": False,
    },
    "default": {
        "color_palette": "cinematic_dark",
        "preferred_fonts": ["Outfit", "Raleway"],
        "animation_preset": "fade_up",
        "background_type": "content_backed",
        "font_weight": "semibold",
        "use_line_accent": False,
    },
}


# Pre-defined complete styles — all cinematic dark
PRESET_STYLES: dict[str, TitleStyle] = {
    "modern_warm": TitleStyle(
        name="modern_warm",
        font_family="Montserrat",
        font_weight="semibold",
        title_size_ratio=0.14,
        letter_spacing=0.02,
        text_color="#FFFFFF",
        text_blend_mode="normal",
        background_type="content_backed",
        background_colors=["#1C1917", "#292524"],
        accent_color="#F59E0B",
        animation_preset="fade_up",
        use_line_accent=False,
    ),
    "elegant_minimal": TitleStyle(
        name="elegant_minimal",
        font_family="Montserrat",
        font_weight="medium",
        title_size_ratio=0.14,
        letter_spacing=0.05,
        text_transform="uppercase",
        text_color="#F5F5F4",
        text_blend_mode="normal",
        background_type="content_backed",
        background_colors=["#0F172A", "#1E293B"],
        accent_color="#06B6D4",
        animation_preset="slow_fade",
        use_line_accent=False,
    ),
    "vintage_charm": TitleStyle(
        name="vintage_charm",
        font_family="Montserrat",
        font_weight="medium",
        title_size_ratio=0.14,
        letter_spacing=0.03,
        text_color="#FAFAF9",
        text_blend_mode="normal",
        background_type="content_backed",
        background_colors=["#1C1917", "#292524"],
        accent_color="#D97706",
        animation_preset="slow_fade",
        use_line_accent=False,
    ),
    "playful_bright": TitleStyle(
        name="playful_bright",
        font_family="Montserrat",
        font_weight="semibold",
        title_size_ratio=0.14,
        letter_spacing=0.01,
        text_color="#FFFFFF",
        text_blend_mode="normal",
        background_type="content_backed",
        background_colors=["#042F2E", "#134E4A"],
        accent_color="#2DD4BF",
        animation_preset="fade_up",
        use_line_accent=False,
    ),
    "soft_romantic": TitleStyle(
        name="soft_romantic",
        font_family="Montserrat",
        font_weight="medium",
        title_size_ratio=0.14,
        letter_spacing=0.02,
        text_color="#FAFAF9",
        text_blend_mode="normal",
        background_type="content_backed",
        background_colors=["#1E1B18", "#3D3428"],
        accent_color="#D97706",
        animation_preset="gentle_scale",
        use_line_accent=False,
    ),
}


def get_style_for_mood(mood: str, randomize: bool = True) -> TitleStyle:
    """Get a TitleStyle based on the video's mood.

    Args:
        mood: The detected mood (e.g., "happy", "calm", "energetic").
        randomize: If True, adds variation within the mood's constraints.

    Returns:
        A TitleStyle configured for the mood.
    """
    profile = MOOD_STYLE_PROFILES.get(mood, MOOD_STYLE_PROFILES["default"])
    palette_name = profile["color_palette"]
    palette = COLOR_PALETTES[palette_name]

    # Montserrat for all titles — consistent with map titles
    font_family = "Montserrat"

    # Select colors
    if randomize:
        bg_colors = random.choice(palette["backgrounds"])
        text_color = random.choice(palette["text_colors"])
        accent_color = random.choice(palette["accents"])
    else:
        bg_colors = palette["backgrounds"][0]
        text_color = palette["text_colors"][0]
        accent_color = palette["accents"][0]

    return TitleStyle(
        name=f"{mood}_style",
        font_family=font_family,
        font_weight=profile["font_weight"],
        title_size_ratio=0.14,
        text_color=text_color,
        text_shadow=False,
        text_blend_mode="normal",
        accent_color=accent_color,
        background_type=profile["background_type"],
        background_colors=bg_colors,
        animation_preset=profile["animation_preset"],
        use_line_accent=profile.get("use_line_accent", False),
        line_position=profile.get("line_position", "above"),
    )


def get_random_style() -> TitleStyle:
    """Get a random style from presets.

    Returns:
        A randomly selected TitleStyle.
    """
    return random.choice(list(PRESET_STYLES.values()))
