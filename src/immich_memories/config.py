"""Configuration management for Immich Memories."""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def expand_env_vars(value: str) -> str:
    """Expand environment variables in a string (${VAR} or $VAR format)."""
    pattern = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, match.group(0))

    return pattern.sub(replacer, value)


class ImmichConfig(BaseModel):
    """Immich server configuration."""

    url: str = Field(default="", description="Immich server URL")
    api_key: str = Field(default="", description="Immich API key")

    @field_validator("url", "api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


class DefaultsConfig(BaseModel):
    """Default settings for video generation."""

    target_duration_minutes: int = Field(default=10, ge=1, le=60)
    output_orientation: Literal["landscape", "portrait", "square", "auto"] = "auto"
    scale_mode: Literal["fit", "fill", "smart_crop"] = "smart_crop"
    transition: Literal["cut", "crossfade", "smart", "none"] = "smart"
    transition_duration: float = Field(default=0.5, ge=0, le=2.0)
    transition_buffer: float = Field(
        default=0.5,
        ge=0,
        le=2.0,
        description="Extra footage (seconds) before/after each clip for smooth fades",
    )


class AnalysisConfig(BaseModel):
    """Settings for video analysis."""

    scene_threshold: float = Field(default=27.0, ge=1.0, le=100.0)
    min_scene_duration: float = Field(default=1.0, ge=0.5, le=10.0)
    duplicate_hash_threshold: int = Field(default=8, ge=0, le=64)
    keyframe_interval: float = Field(default=1.0, ge=0.5, le=5.0)

    # Scene detection settings
    use_scene_detection: bool = Field(
        default=True,
        description="Use scene detection for natural boundaries (enabled by default)",
    )
    max_segment_duration: float = Field(
        default=15.0,
        ge=2.0,
        le=30.0,
        description="Maximum segment duration in seconds (long scenes are subdivided)",
    )
    min_segment_duration: float = Field(
        default=2.0,
        ge=0.5,
        le=5.0,
        description="Minimum segment duration in seconds (clips shorter than this are discarded)",
    )
    optimal_clip_duration: float = Field(
        default=5.0,
        ge=2.0,
        le=15.0,
        description="Base sweet spot clip duration in seconds (scales up for longer sources)",
    )
    max_optimal_duration: float = Field(
        default=15.0,
        ge=5.0,
        le=30.0,
        description="Maximum optimal clip duration for long source videos",
    )
    target_extraction_ratio: float = Field(
        default=0.25,
        ge=0.05,
        le=0.5,
        description="Target ratio of clip to source duration (0.25 = 25% of source)",
    )

    # Speed optimization: downscale videos for analysis
    enable_downscaling: bool = Field(
        default=True,
        description="Downscale videos before analysis for speed (~3-5x faster)",
    )
    analysis_resolution: int = Field(
        default=480,
        ge=240,
        le=1080,
        description="Target height for analysis (480 = 480p). Lower = faster.",
    )

    # Unified analysis settings (audio-aware boundaries)
    use_unified_analysis: bool = Field(
        default=True,
        description="Use unified analysis with audio-aware boundaries to avoid mid-sentence cuts",
    )
    cut_point_merge_tolerance: float = Field(
        default=0.5,
        ge=0.1,
        le=2.0,
        description="Time window (seconds) for merging nearby visual/audio boundaries",
    )
    silence_threshold_db: float = Field(
        default=-40.0,
        ge=-60.0,
        le=-10.0,
        description="Audio level threshold in dB for silence detection (lower = more sensitive)",
    )
    min_silence_duration: float = Field(
        default=0.2,
        ge=0.1,
        le=1.0,
        description="Minimum duration (seconds) of quiet audio to count as a silence gap",
    )


class HardwareAccelConfig(BaseModel):
    """Hardware acceleration settings."""

    # Auto-detect available hardware by default
    enabled: bool = Field(default=True, description="Enable hardware acceleration")

    # Preferred backend: auto will detect best available
    backend: Literal["auto", "nvidia", "apple", "vaapi", "qsv", "none"] = Field(
        default="auto", description="Hardware acceleration backend"
    )

    # Encoding settings
    encoder_preset: Literal["fast", "balanced", "quality"] = Field(
        default="balanced", description="Encoder speed/quality tradeoff"
    )

    # GPU device index (for multi-GPU systems)
    device_index: int = Field(default=0, ge=0, description="GPU device index")

    # Use GPU for frame analysis (OpenCV CUDA, etc.)
    gpu_analysis: bool = Field(
        default=True, description="Use GPU for video analysis when available"
    )

    # Decode on GPU (can speed up processing significantly)
    gpu_decode: bool = Field(default=True, description="Use hardware video decoding")

    # Memory limit for GPU operations (MB, 0 = no limit)
    gpu_memory_limit: int = Field(
        default=0, ge=0, description="GPU memory limit in MB (0 = unlimited)"
    )


class OutputConfig(BaseModel):
    """Output settings."""

    directory: str = Field(default="~/Videos/Memories")
    format: Literal["mp4", "mov"] = "mp4"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    codec: Literal["h264", "h265", "prores"] = "h264"
    crf: int = Field(default=18, ge=0, le=51)

    @property
    def output_path(self) -> Path:
        """Get the expanded output directory path."""
        return Path(self.directory).expanduser()

    @property
    def resolution_tuple(self) -> tuple[int, int]:
        """Get resolution as (width, height) tuple for landscape orientation."""
        resolutions = {
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "4k": (3840, 2160),
        }
        return resolutions[self.resolution]


class CacheConfig(BaseModel):
    """Cache settings."""

    directory: str = Field(default="~/.immich-memories/cache")
    database: str = Field(default="~/.immich-memories/cache.db")
    max_age_days: int = Field(default=30, ge=1, le=365)

    # Video file cache settings
    video_cache_enabled: bool = Field(
        default=True, description="Enable local video file caching to avoid re-downloads"
    )
    video_cache_max_size_gb: float = Field(
        default=10.0, ge=1, le=500, description="Maximum video cache size in GB"
    )
    video_cache_max_age_days: int = Field(
        default=7, ge=1, le=365, description="Maximum age of cached video files in days"
    )

    @property
    def cache_path(self) -> Path:
        """Get the expanded cache directory path."""
        return Path(self.directory).expanduser()

    @property
    def database_path(self) -> Path:
        """Get the expanded database path."""
        return Path(self.database).expanduser()

    @property
    def video_cache_path(self) -> Path:
        """Get the video cache directory path."""
        return self.cache_path / "video-cache"


class LLMConfig(BaseModel):
    """Shared LLM provider settings for Ollama and OpenAI."""

    # Ollama settings (preferred - local, privacy-friendly)
    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Ollama API URL",
    )
    ollama_model: str = Field(
        default="llava",
        description="Ollama vision model (llava, bakllava, qwen2-vl, etc.)",
    )

    # OpenAI settings (fallback)
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (fallback if Ollama unavailable)",
    )
    openai_model: str = Field(
        default="gpt-4.1-nano",
        description="OpenAI model for vision tasks (gpt-4.1-nano, gpt-5-mini, gpt-5.2, etc.)",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API base URL (for Azure, on-prem, or compatible endpoints)",
    )

    # Provider preference
    provider: Literal["ollama", "openai", "auto"] = Field(
        default="auto",
        description="LLM provider (auto = try Ollama first, fallback to OpenAI)",
    )

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


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


class Config(BaseSettings):
    """Main configuration for Immich Memories."""

    model_config = SettingsConfigDict(
        env_prefix="IMMICH_MEMORIES_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    immich: ImmichConfig = Field(default_factory=ImmichConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    hardware: HardwareAccelConfig = Field(default_factory=HardwareAccelConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    musicgen: MusicGenConfig = Field(default_factory=MusicGenConfig)
    ace_step: ACEStepConfig = Field(default_factory=ACEStepConfig)
    content_analysis: ContentAnalysisConfig = Field(default_factory=ContentAnalysisConfig)
    audio_content: AudioContentConfig = Field(default_factory=AudioContentConfig)
    title_screens: TitleScreenConfig = Field(default_factory=TitleScreenConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> Config:
        """Load configuration from a YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    def save_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def get_default_path(cls) -> Path:
        """Get the default configuration file path."""
        return Path.home() / ".immich-memories" / "config.yaml"


# Global configuration instance
_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Get the global configuration instance.

    Args:
        reload: If True, reload from disk even if already loaded.

    Returns:
        The global Config instance.
    """
    global _config

    if _config is None or reload:
        config_path = Config.get_default_path()
        _config = Config.from_yaml(config_path)

        # Override with environment variables
        if url := os.environ.get("IMMICH_URL"):
            _config.immich.url = url
        if api_key := os.environ.get("IMMICH_API_KEY"):
            _config.immich.api_key = api_key
        if openai_key := os.environ.get("OPENAI_API_KEY"):
            _config.llm.openai_api_key = openai_key
        if pixabay_key := os.environ.get("PIXABAY_API_KEY"):
            _config.audio.pixabay_api_key = pixabay_key

        # MusicGen env var overrides (also supported via IMMICH_MEMORIES_MUSICGEN__*)
        if musicgen_enabled := os.environ.get("MUSICGEN_ENABLED"):
            _config.musicgen.enabled = musicgen_enabled.lower() in ("true", "1", "yes")
        if musicgen_url := os.environ.get("MUSICGEN_BASE_URL"):
            _config.musicgen.base_url = musicgen_url
        if musicgen_key := os.environ.get("MUSICGEN_API_KEY"):
            _config.musicgen.api_key = musicgen_key

        # ACE-Step env var overrides (also supported via IMMICH_MEMORIES_ACE_STEP__*)
        if ace_step_enabled := os.environ.get("ACE_STEP_ENABLED"):
            _config.ace_step.enabled = ace_step_enabled.lower() in ("true", "1", "yes")
        if (ace_step_mode := os.environ.get("ACE_STEP_MODE")) and ace_step_mode in ("lib", "api"):
            _config.ace_step.mode = ace_step_mode  # type: ignore[assignment]
        if ace_step_url := os.environ.get("ACE_STEP_API_URL"):
            _config.ace_step.api_url = ace_step_url

    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config


def init_config_dir() -> Path:
    """Initialize the configuration directory structure.

    Returns:
        Path to the configuration directory.
    """
    logger = logging.getLogger(__name__)
    config_dir = Path.home() / ".immich-memories"
    config_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = config_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    projects_dir = config_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Enforce restrictive permissions on config directory (owner-only access)
    # This protects API keys and cached data on multi-user systems.
    try:
        for d in (config_dir, cache_dir, projects_dir):
            d.chmod(0o700)

        # Warn if config file has overly permissive permissions
        config_file = config_dir / "config.yaml"
        if config_file.exists():
            mode = config_file.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                logger.warning(
                    "Config file %s is readable by other users. Run: chmod 600 %s",
                    config_file,
                    config_file,
                )
    except OSError:
        pass  # Skip on platforms where chmod is not supported (e.g., some Windows setups)

    return config_dir
