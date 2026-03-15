"""Core configuration models for Immich Memories.

Contains server, defaults, analysis, hardware, output, cache, and LLM config models.
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
    scale_mode: Literal["fit", "fill", "smart_crop"] = "smart_crop"
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
