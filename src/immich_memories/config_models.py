"""Configuration models for Immich Memories.

Contains all config models: server, defaults, analysis, hardware, output,
cache, LLM, audio, music generation, content analysis, title screen, and upload.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    scale_mode: Literal["fit", "fill", "smart_crop", "blur"] = "blur"
    transition: Literal["cut", "crossfade", "smart", "none"] = "smart"
    transition_duration: float = Field(default=0.5, ge=0, le=2.0)
    transition_buffer: float = Field(
        default=0.5,
        ge=0,
        le=2.0,
        description="Extra footage (seconds) before/after each clip for smooth fades",
    )


_CLIP_STYLE_PRESETS: dict[str, dict[str, float]] = {
    "fast-cuts": {
        "optimal_clip_duration": 3.0,
        "max_optimal_duration": 6.0,
        "target_extraction_ratio": 0.3,
        "max_segment_duration": 8.0,
        "min_segment_duration": 1.5,
    },
    "balanced": {
        "optimal_clip_duration": 5.0,
        "max_optimal_duration": 10.0,
        "target_extraction_ratio": 0.4,
        "max_segment_duration": 15.0,
        "min_segment_duration": 2.0,
    },
    "long-cuts": {
        "optimal_clip_duration": 8.0,
        "max_optimal_duration": 15.0,
        "target_extraction_ratio": 0.5,
        "max_segment_duration": 25.0,
        "min_segment_duration": 3.0,
    },
}


class AnalysisConfig(BaseModel):
    """Settings for video analysis."""

    scene_threshold: float = Field(default=27.0, ge=1.0, le=100.0)
    min_scene_duration: float = Field(default=1.0, ge=0.5, le=10.0)
    duplicate_hash_threshold: int = Field(default=8, ge=0, le=64)
    keyframe_interval: float = Field(default=1.0, ge=0.5, le=5.0)

    # Clip style preset — sets the 5 duration params below.
    # Explicit overrides win over the preset.
    clip_style: Literal["fast-cuts", "balanced", "long-cuts"] | None = Field(
        default=None,
        description="Clip pacing preset (fast-cuts | balanced | long-cuts). "
        "Sets duration params below; explicit overrides win.",
    )

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

    @model_validator(mode="before")
    @classmethod
    def apply_clip_style(cls, data: dict) -> dict:
        """Expand clip_style preset into duration params (explicit overrides win)."""
        if not isinstance(data, dict):
            return data
        style = data.get("clip_style")
        if style is None:
            return data
        if style not in _CLIP_STYLE_PRESETS:
            raise ValueError(
                f"Invalid clip_style '{style}'. Choose from: {', '.join(_CLIP_STYLE_PRESETS)}"
            )
        preset = _CLIP_STYLE_PRESETS[style]
        for key, value in preset.items():
            if key not in data:
                data[key] = value
        return data

    # Live Photo settings
    include_live_photos: bool = Field(
        default=False,
        description="Include Live Photo video clips (opt-in, fetches 3s clips from iPhone Live Photos)",
    )
    live_photo_merge_window_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Max gap between Live Photos to group into a burst cluster",
    )
    live_photo_min_burst_count: int = Field(
        default=3,
        ge=2,
        le=20,
        description="Minimum photos in a cluster to qualify as a burst (merged into one clip)",
    )

    @model_validator(mode="after")
    def validate_duration_constraints(self) -> AnalysisConfig:
        """Ensure min_segment_duration < max_segment_duration and related constraints."""
        if self.min_segment_duration >= self.max_segment_duration:
            raise ValueError(
                f"min_segment_duration ({self.min_segment_duration}) must be less than "
                f"max_segment_duration ({self.max_segment_duration})"
            )
        if self.min_segment_duration >= self.optimal_clip_duration:
            raise ValueError(
                f"min_segment_duration ({self.min_segment_duration}) must be less than "
                f"optimal_clip_duration ({self.optimal_clip_duration})"
            )
        if self.optimal_clip_duration > self.max_optimal_duration:
            raise ValueError(
                f"optimal_clip_duration ({self.optimal_clip_duration}) must not exceed "
                f"max_optimal_duration ({self.max_optimal_duration})"
            )
        return self

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
    """Shared LLM provider settings.

    Two providers: "ollama" (native Ollama API) or "openai-compatible"
    (any server speaking /v1/chat/completions — OpenAI, Groq, mlx-vlm, vLLM, etc.).
    """

    provider: Literal["ollama", "openai-compatible"] = Field(
        default="openai-compatible",
        description="LLM provider: 'ollama' or 'openai-compatible'",
    )
    base_url: str = Field(
        default="http://localhost:8080/v1",
        description="API base URL",
    )
    model: str = Field(
        default="",
        description="Model name",
    )
    api_key: str = Field(
        default="",
        description="API key (optional, only needed for cloud APIs)",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="HTTP timeout for LLM requests in seconds (increase for slow local models)",
    )

    @field_validator("api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


class ServerConfig(BaseModel):
    """UI server settings (host, port)."""

    host: str = Field(default="0.0.0.0", description="Listen address (IPv4, IPv6, or hostname)")  # noqa: S104
    port: int = Field(default=8080, ge=1, le=65535, description="Listen port")


class TripsConfig(BaseModel):
    """Trip detection configuration: homebase location and clustering thresholds."""

    homebase_latitude: float = Field(default=0.0, description="Home latitude (required for trips)")
    homebase_longitude: float = Field(
        default=0.0, description="Home longitude (required for trips)"
    )
    min_distance_km: float = Field(default=50, ge=1, description="Min km from home to count")
    min_duration_days: int = Field(default=2, ge=1, description="Min days to qualify as a trip")
    max_gap_days: int = Field(default=2, ge=1, description="Max gap before splitting trips")

    def validate_homebase(self) -> None:
        """Raise if homebase is still at Null Island (0,0)."""
        if self.homebase_latitude == self.homebase_longitude == 0.0:
            msg = (
                "Set your home coordinates in config "
                "(trips.homebase_latitude / trips.homebase_longitude)"
            )
            raise ValueError(msg)


class AudioConfig(BaseModel):
    """Audio and music settings."""

    # Automatic music selection
    auto_music: bool = Field(
        default=False, description="Automatically select background music based on video mood"
    )

    # Music sources
    music_source: Literal["local", "musicgen", "ace_step"] = Field(
        default="musicgen",
        description="Source for automatic music selection (musicgen = MusicGen API, ace_step = ACE-Step local/API)",
    )
    local_music_dir: str = Field(
        default="~/Music/Memories", description="Directory for local music library"
    )

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
        description="Generation mode: 'lib' for local library, 'api' for remote REST server",
    )
    api_url: str = Field(
        default="http://localhost:8000",
        description="ACE-Step REST API server URL (only used in API mode)",
    )
    api_key: str = Field(
        default="",
        description="API key for ACE-Step server authentication (optional)",
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
    use_panns: bool = Field(
        default=True,
        description="Use PANNs ML model for audio classification (requires torch)",
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


class ScoringPriorityConfig(BaseModel):
    """User-facing scoring knobs (Tier 1)."""

    people: Literal["low", "medium", "high"] = "high"
    quality: Literal["low", "medium", "high"] = "medium"
    moment: Literal["low", "medium", "high"] = "medium"


class PhotoConfig(BaseModel):
    """Photo-to-video animation settings."""

    enabled: bool = Field(default=False, description="Include photos in memory videos")
    max_ratio: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Maximum fraction of clips that can be photos (0.50 = 50%)",
    )
    duration: float = Field(
        default=4.0,
        ge=1.0,
        le=10.0,
        description="Duration per single photo clip in seconds",
    )
    collage_duration: float = Field(
        default=6.0,
        ge=2.0,
        le=15.0,
        description="Duration per collage clip in seconds",
    )
    animation_mode: Literal["auto", "ken_burns", "face_zoom", "blur_bg"] = Field(
        default="auto",
        description="Animation mode (auto selects per photo based on content)",
    )
    enable_collage: bool = Field(
        default=True,
        description="Enable multi-photo collage for photo series",
    )
    series_gap_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        description="Max gap between photos to group as a series",
    )
    zoom_factor: float = Field(
        default=1.15,
        ge=1.0,
        le=2.0,
        description="Ken Burns zoom factor (1.15 = 15% zoom)",
    )
    score_penalty: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Score reduction for photos vs videos (0.2 = photos score 80% of videos)",
    )


class UploadConfig(BaseModel):
    """Upload generated videos back to Immich."""

    enabled: bool = Field(default=False, description="Upload generated video to Immich")
    album_name: str | None = Field(
        default=None, description="Album name (created if missing, reused if exists)"
    )
