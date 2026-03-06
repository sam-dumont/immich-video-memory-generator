"""Visual style definitions for title screens.

This module contains:
- TitleStyle dataclass with all visual properties
- Color palettes (warm, avoiding dark colors)
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

    # Typography
    font_family: str = "Outfit"
    font_weight: Literal["light", "regular", "medium", "semibold"] = "medium"
    title_size_ratio: float = 0.10  # Relative to screen height (0.08 - 0.15)
    subtitle_size_ratio: float = 0.6  # Relative to title size (0.5 - 0.7)
    letter_spacing: float = 0.02  # em units (-0.02 to 0.1)
    text_transform: Literal["none", "uppercase", "capitalize"] = "none"

    # Colors
    text_color: str = "#2D2D2D"
    text_shadow: bool = False  # Replaced by blend mode
    text_blend_mode: Literal["normal", "multiply", "overlay", "soft_light"] = "multiply"
    accent_color: str = "#F59E0B"

    # Background
    background_type: Literal["solid_gradient", "soft_gradient", "vignette"] = (
        "soft_gradient"
    )
    background_colors: list[str] = field(
        default_factory=lambda: ["#FFF5E6", "#FFE4CC"]
    )
    background_angle: int = 135  # degrees for linear gradient

    # Animation preset name (see animations.py)
    animation_preset: str = "fade_up"

    # Decorative elements
    use_line_accent: bool = True
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


# Color palettes (avoiding dark/black colors as per spec)
COLOR_PALETTES: dict[str, dict] = {
    "warm_vibrant": {
        "backgrounds": [
            ["#FFF5E6", "#FFE4CC"],  # Warm cream
            ["#FFF0F5", "#FFE4EC"],  # Soft pink
            ["#FFFBEB", "#FEF3C7"],  # Warm yellow
        ],
        "text_colors": ["#2D2D2D", "#3D3D3D", "#4A4A4A"],
        "accents": ["#F59E0B", "#EC4899", "#8B5CF6"],
    },
    "soft_pastels": {
        "backgrounds": [
            ["#F0F9FF", "#E0F2FE"],  # Soft blue
            ["#FDF4FF", "#FAE8FF"],  # Soft purple
            ["#F0FDF4", "#DCFCE7"],  # Soft green
        ],
        "text_colors": ["#374151", "#4B5563", "#6B7280"],
        "accents": ["#06B6D4", "#A855F7", "#10B981"],
    },
    "warm_muted": {
        "backgrounds": [
            ["#FEF7ED", "#FED7AA"],  # Muted orange
            ["#FFFBEB", "#FDE68A"],  # Muted yellow
            ["#FDF2F8", "#FBCFE8"],  # Muted pink
        ],
        "text_colors": ["#44403C", "#57534E", "#78716C"],
        "accents": ["#D97706", "#DB2777", "#7C3AED"],
    },
    "bright_warm": {
        "backgrounds": [
            ["#FEF9C3", "#FDE047"],  # Bright yellow
            ["#FFE4E6", "#FDA4AF"],  # Bright pink
            ["#FFEDD5", "#FDBA74"],  # Bright orange
        ],
        "text_colors": ["#1C1917", "#292524", "#3D3D3D"],
        "accents": ["#EAB308", "#F43F5E", "#F97316"],
    },
    "bright_colorful": {
        "backgrounds": [
            ["#ECFEFF", "#A5F3FC"],  # Cyan
            ["#FDF2F8", "#F9A8D4"],  # Pink
            ["#F0FDF4", "#86EFAC"],  # Green
        ],
        "text_colors": ["#0F172A", "#1E293B", "#334155"],
        "accents": ["#06B6D4", "#EC4899", "#22C55E"],
    },
    "soft_warm": {
        "backgrounds": [
            ["#FFFBEB", "#FEF3C7"],  # Soft amber
            ["#FFF7ED", "#FED7AA"],  # Soft orange
            ["#FEF2F2", "#FECACA"],  # Soft red
        ],
        "text_colors": ["#451A03", "#78350F", "#7C2D12"],
        "accents": ["#F59E0B", "#EA580C", "#DC2626"],
    },
    "elegant_neutral": {
        "backgrounds": [
            ["#FAFAF9", "#E7E5E4"],  # Stone
            ["#F9FAFB", "#E5E7EB"],  # Gray
            ["#FFFBEB", "#FEF3C7"],  # Warm touch
        ],
        "text_colors": ["#1C1917", "#292524", "#44403C"],
        "accents": ["#78716C", "#A8A29E", "#D6D3D1"],
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


# Mood-to-style mapping
MOOD_STYLE_PROFILES: dict[str, dict] = {
    "happy": {
        "color_palette": "warm_vibrant",
        "preferred_fonts": ["Quicksand", "Outfit"],
        "animation_preset": "fade_up",
        "background_type": "soft_gradient",
        "font_weight": "medium",
        "use_line_accent": True,
    },
    "calm": {
        "color_palette": "soft_pastels",
        "preferred_fonts": ["Raleway", "JosefinSans"],
        "animation_preset": "slow_fade",
        "background_type": "solid_gradient",
        "font_weight": "light",
        "use_line_accent": False,
    },
    "energetic": {
        "color_palette": "bright_warm",
        "preferred_fonts": ["Outfit", "Quicksand"],
        "animation_preset": "smooth_slide",
        "background_type": "soft_gradient",
        "font_weight": "semibold",
        "use_line_accent": True,
    },
    "nostalgic": {
        "color_palette": "warm_muted",
        "preferred_fonts": ["JosefinSans", "Raleway"],
        "animation_preset": "slow_fade",
        "background_type": "vignette",
        "font_weight": "light",
        "use_line_accent": True,
        "line_position": "both",
    },
    "romantic": {
        "color_palette": "soft_warm",
        "preferred_fonts": ["JosefinSans", "Raleway"],
        "animation_preset": "gentle_scale",
        "background_type": "soft_gradient",
        "font_weight": "light",
        "use_line_accent": True,
    },
    "playful": {
        "color_palette": "bright_colorful",
        "preferred_fonts": ["Quicksand", "Outfit"],
        "animation_preset": "fade_up",
        "background_type": "soft_gradient",
        "font_weight": "medium",
        "use_line_accent": True,
    },
    "peaceful": {
        "color_palette": "soft_pastels",
        "preferred_fonts": ["Raleway", "JosefinSans"],
        "animation_preset": "slow_fade",
        "background_type": "vignette",
        "font_weight": "light",
        "use_line_accent": False,
    },
    "exciting": {
        "color_palette": "bright_warm",
        "preferred_fonts": ["Outfit", "Quicksand"],
        "animation_preset": "smooth_slide",
        "background_type": "soft_gradient",
        "font_weight": "semibold",
        "use_line_accent": True,
    },
    "default": {
        "color_palette": "warm_vibrant",
        "preferred_fonts": ["Outfit", "Raleway"],
        "animation_preset": "fade_up",
        "background_type": "soft_gradient",
        "font_weight": "medium",
        "use_line_accent": True,
    },
}


# Pre-defined complete styles
PRESET_STYLES: dict[str, TitleStyle] = {
    "modern_warm": TitleStyle(
        name="modern_warm",
        font_family="Outfit",
        font_weight="medium",
        title_size_ratio=0.10,
        letter_spacing=0.02,
        text_color="#2D2D2D",
        background_type="soft_gradient",
        background_colors=["#FFF5E6", "#FFE4CC"],
        accent_color="#F59E0B",
        animation_preset="fade_up",
        use_line_accent=True,
        line_position="above",
    ),
    "elegant_minimal": TitleStyle(
        name="elegant_minimal",
        font_family="Raleway",
        font_weight="light",
        title_size_ratio=0.09,
        letter_spacing=0.05,
        text_transform="uppercase",
        text_color="#374151",
        background_type="solid_gradient",
        background_colors=["#F0F9FF", "#E0F2FE"],
        accent_color="#06B6D4",
        animation_preset="slow_fade",
        use_line_accent=False,
    ),
    "vintage_charm": TitleStyle(
        name="vintage_charm",
        font_family="JosefinSans",
        font_weight="light",
        title_size_ratio=0.11,
        letter_spacing=0.03,
        text_color="#44403C",
        background_type="vignette",
        background_colors=["#FFFBF5", "#F5E6D3"],
        accent_color="#D97706",
        animation_preset="slow_fade",
        use_line_accent=True,
        line_position="both",
    ),
    "playful_bright": TitleStyle(
        name="playful_bright",
        font_family="Quicksand",
        font_weight="medium",
        title_size_ratio=0.10,
        letter_spacing=0.01,
        text_color="#1C1917",
        background_type="soft_gradient",
        background_colors=["#FEF9C3", "#FDE047"],
        accent_color="#EAB308",
        animation_preset="fade_up",
        use_line_accent=True,
        line_position="above",
    ),
    "soft_romantic": TitleStyle(
        name="soft_romantic",
        font_family="JosefinSans",
        font_weight="regular",
        title_size_ratio=0.10,
        letter_spacing=0.02,
        text_color="#451A03",
        background_type="soft_gradient",
        background_colors=["#FFF0F5", "#FFE4EC"],
        accent_color="#EC4899",
        animation_preset="gentle_scale",
        use_line_accent=True,
        line_position="below",
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

    # Select font
    preferred_fonts = profile["preferred_fonts"]
    font_family = random.choice(preferred_fonts) if randomize else preferred_fonts[0]

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
        title_size_ratio=0.10 if randomize else 0.10 + random.uniform(-0.01, 0.01),
        text_color=text_color,
        accent_color=accent_color,
        background_type=profile["background_type"],
        background_colors=bg_colors,
        animation_preset=profile["animation_preset"],
        use_line_accent=profile.get("use_line_accent", True),
        line_position=profile.get("line_position", "above"),
    )


def get_random_style() -> TitleStyle:
    """Get a random style from presets.

    Returns:
        A randomly selected TitleStyle.
    """
    return random.choice(list(PRESET_STYLES.values()))
