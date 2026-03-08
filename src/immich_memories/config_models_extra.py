"""Additional configuration models for Immich Memories.

Contains audio, music generation, content analysis, title screen,
and audio content config models. Split from config_models.py
to keep files under 500 lines.
Re-exported from config.py for backwards compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars


class AudioConfig(BaseModel):
    """Audio and music settings."""

    # Automatic music selection
    auto_music: bool = Field(
        default=False, description="Automatically select background music based on video mood"
    )

    # Music sources
    music_source: Literal["pixabay", "local", "musicgen", "ace_step"] = Field(
        default="pixabay",
        description="Source for automatic music selection (musicgen = MusicGen API, ace_step = ACE-Step local/API)",
    )
    local_music_dir: str = Field(
        default="~/Music/Memories", description="Directory for local music library"
    )
    pixabay_api_key: str = Field(default="", description="Pixabay API key (optional)")

    # Audio ducking settings
    ducking_threshold: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Sensitivity for voice detection (lower = more sensitive)",
    )
    ducking_ratio: float = Field(
        default=6.0, ge=1.0, le=20.0, description="How much to lower music when speech detected"
    )
    music_volume_db: float = Field(
        default=-6.0, ge=-20.0, le=0.0, description="Base music volume in dB"
    )
    fade_in_seconds: float = Field(
        default=2.0, ge=0.0, le=10.0, description="Music fade in duration"
    )
    fade_out_seconds: float = Field(
        default=3.0, ge=0.0, le=10.0, description="Music fade out duration"
    )

    @field_validator("pixabay_api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v

    @property
    def local_music_path(self) -> Path:
        """Get the expanded local music directory path."""
        return Path(self.local_music_dir).expanduser()


class MusicGenConfig(BaseModel):
    """Settings for AI music generation via MusicGen API."""

    enabled: bool = Field(
        default=False,
        description="Enable AI music generation using MusicGen API",
    )
    base_url: str = Field(
        default="http://localhost:8000",
        description="MusicGen API server URL",
    )
    api_key: str = Field(
        default="",
        description="MusicGen API key for authentication",
    )
    timeout_seconds: int = Field(
        default=10800,  # 3 hours
        ge=60,
        le=18000,  # Up to 5 hours max
        description="Maximum time to wait per music generation job (seconds)",
    )
    num_versions: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Number of music versions to generate for selection",
    )
    hemisphere: str = Field(
        default="north",
        description="Hemisphere for seasonal music prompts ('north' or 'south')",
    )

    @field_validator("api_key", "base_url", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


class ACEStepConfig(BaseModel):
    """Settings for ACE-Step music generation.

    ACE-Step 1.5 can run locally as a Python library (preferred for desktop)
    or via a remote Gradio API server.
    """

    enabled: bool = Field(
        default=False,
        description="Enable ACE-Step music generation",
    )
    mode: Literal["lib", "api"] = Field(
        default="lib",
        description="Generation mode: 'lib' for local library, 'api' for remote Gradio server",
    )
    api_url: str = Field(
        default="http://localhost:7860",
        description="ACE-Step Gradio server URL (only used in API mode)",
    )
    model_variant: str = Field(
        default="turbo",
        description="Model variant: 'turbo' (8 steps, fast) or 'base' (50 steps, quality)",
    )
    lm_model_size: str = Field(
        default="1.7B",
        description="Language model size: '0.6B', '1.7B', or '4B'",
    )
    use_lm: bool = Field(
        default=True,
        description="Use language model for 'thinking mode' (disable to save memory/time)",
    )
    bf16: bool = Field(
        default=True,
        description="Use bfloat16 precision (set False for Pascal/older GPUs)",
    )
    num_versions: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Number of music versions to generate for selection",
    )
    hemisphere: str = Field(
        default="north",
        description="Hemisphere for seasonal music prompts ('north' or 'south')",
    )
    timeout_seconds: int = Field(
        default=3600,
        ge=60,
        le=18000,
        description="Maximum time per generation job (seconds)",
    )

    @field_validator("api_url", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


class ContentAnalysisConfig(BaseModel):
    """Settings for LLM-based content analysis."""

    enabled: bool = Field(
        default=False,
        description="Enable LLM content analysis (slower but more intelligent scoring)",
    )
    weight: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Weight of content score in overall scoring (0-1)",
    )

    # Analysis parameters
    analyze_frames: int = Field(
        default=2,
        ge=1,
        le=4,
        description="Number of frames to analyze per segment (reduced from 3 for speed)",
    )
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold to use content analysis score",
    )

    # Frame optimization parameters
    frame_max_height: int = Field(
        default=480,
        ge=240,
        le=1080,
        description="Max frame height for LLM analysis (480=fast/cheap, 720=balanced, 1080=quality)",
    )
    openai_image_detail: Literal["low", "high", "auto"] = Field(
        default="low",
        description="OpenAI image detail level (low=85 tokens/cheap, high=1889 tokens/detailed)",
    )


class TitleScreenConfig(BaseModel):
    """Settings for title screens, month dividers, and ending screens."""

    enabled: bool = Field(
        default=True,
        description="Enable title screens, month dividers, and ending screens",
    )

    # Timing
    title_duration: float = Field(
        default=3.5,
        ge=1.0,
        le=10.0,
        description="Duration of opening title screen in seconds",
    )
    month_divider_duration: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="Duration of month divider screens in seconds",
    )
    ending_duration: float = Field(
        default=7.0,
        ge=2.0,
        le=15.0,
        description="Duration of ending screen in seconds",
    )
    animation_duration: float = Field(
        default=0.5,
        ge=0.2,
        le=2.0,
        description="Duration of text animations in seconds",
    )

    # Localization
    locale: Literal["en", "fr", "auto"] = Field(
        default="auto",
        description="Language for title text (en, fr, or auto-detect)",
    )

    # Visual style
    style_mode: Literal["auto", "random"] = Field(
        default="auto",
        description="Style selection mode (auto = mood-based, random = random selection)",
    )
    animated_background: bool = Field(
        default=True,
        description="Enable subtle background animations (gradient shift, color pulse)",
    )
    show_decorative_lines: bool = Field(
        default=True,
        description="Show decorative line accents on title screens",
    )

    # Color preferences
    avoid_dark_colors: bool = Field(
        default=True,
        description="Avoid dark/black color schemes, prefer warm light colors",
    )
    minimum_brightness: int = Field(
        default=100,
        ge=0,
        le=255,
        description="Minimum brightness for colors (0-255)",
    )

    # Month dividers
    show_month_dividers: bool = Field(
        default=True,
        description="Show month divider screens when video spans multiple months",
    )
    month_divider_threshold: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Minimum clips needed in a month to show its divider",
    )

    # Name display
    use_first_name_only: bool = Field(
        default=True,
        description="Use only the first name for titles (e.g., 'John' instead of 'John Smith')",
    )

    # Custom font
    custom_font_path: str | None = Field(
        default=None,
        description="Path to custom font file (TTF/OTF)",
    )


class AudioContentConfig(BaseModel):
    """Settings for audio content analysis (laughter/speech detection)."""

    enabled: bool = Field(
        default=False,
        description="Enable audio content analysis for laughter/speech detection",
    )
    weight: float = Field(
        default=0.15,
        ge=0.0,
        le=0.5,
        description="Weight of audio content score in overall scoring (0-0.5)",
    )

    # Detection settings
    use_yamnet: bool = Field(
        default=True,
        description="Use YAMNet ML model for audio classification (requires tensorflow)",
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0.1,
        le=0.9,
        description="Minimum confidence for audio event detection",
    )
    laughter_confidence: float = Field(
        default=0.2,
        ge=0.1,
        le=0.5,
        description="Lower confidence threshold for laughter/baby sounds (often quieter)",
    )

    # Laughter bonus
    laughter_bonus: float = Field(
        default=0.1,
        ge=0.0,
        le=0.3,
        description="Extra score bonus for segments containing laughter",
    )

    # Boundary protection
    protect_laughter: bool = Field(
        default=True,
        description="Avoid cutting during laughter events",
    )
    protect_speech: bool = Field(
        default=True,
        description="Avoid cutting during speech events",
    )
