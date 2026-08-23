"""Configuration models for Immich Memories.

Contains all config models: server, defaults, analysis, hardware, output,
cache, LLM, audio, music generation, content analysis, title screen, and upload.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from immich_memories.api.compatibility import ApiVersionPolicy
from immich_memories.processing.encoding_plan import HdrMode

logger = logging.getLogger(__name__)

_ENV_REFERENCE = re.compile(r"\$\{([^}]+)\}")
# Only reported, never expanded. `$USER` is set on every login shell, so a
# password like `S3cret$USER!` used to become `S3cretsam!` -- and the user sees
# "wrong password" with no path to the cause.
_BARE_ENV_REFERENCE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_env_vars(value: str) -> str:
    """Expand `${VAR}` references in a config value.

    Only the delimited form expands. A bare `$NAME` is left exactly as written,
    because these fields hold passwords and API keys, and a `$` in a secret is
    ordinary: silently turning part of a credential into the value of an
    environment variable is worse than not expanding it, since the failure
    surfaces as a rejected login rather than as a config error.
    """

    def replacer(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(0))

    expanded = _ENV_REFERENCE.sub(replacer, value)
    _warn_about_bare_references(expanded)
    return expanded


def _warn_about_bare_references(value: str) -> None:
    """Tell anyone relying on the old bare `$NAME` form why it stopped working.

    Dropping a documented form silently would trade one quiet surprise for
    another, so a bare reference that names a variable which actually exists is
    reported. A `$` that matches nothing stays silent -- that is just a password.
    """
    for match in _BARE_ENV_REFERENCE.finditer(value):
        if match.group(1) in os.environ:
            logger.warning(
                "Config value contains %s, which is no longer expanded; "
                "write ${%s} if you meant the environment variable.",
                match.group(0),
                match.group(1),
            )


class ImmichConfig(BaseModel):
    """Immich server configuration."""

    url: str = Field(default="", description="Immich server URL")
    api_key: str = Field(default="", description="Immich API key")
    api_version: ApiVersionPolicy = ApiVersionPolicy.AUTO

    @field_serializer("api_version")
    def serialize_api_version(self, value: ApiVersionPolicy) -> str:
        """Serialize the policy as a portable YAML/JSON string."""
        return value.value

    @field_validator("url", "api_key", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        """Expand environment variables in config values."""
        if isinstance(v, str):
            return expand_env_vars(v)
        return v


class DefaultsConfig(BaseModel):
    """Default settings for video generation."""

    scale_mode: Literal["fit", "fill", "smart_crop", "blur"] = "blur"
    transition: Literal["cut", "crossfade", "smart", "none"] = "smart"
    transition_duration: float = Field(default=0.5, ge=0, le=2.0)


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

    download_workers: int = Field(
        default=3,
        ge=1,
        le=8,
        description="Concurrent isolated clients used for video and thumbnail prefetching",
    )
    max_album_assets: int = Field(
        default=10000,
        ge=1,
        description="Per media type, the most assets read from an album (#270). "
        "Smart albums reach tens of thousands; Immich returns newest first, so a "
        "larger album is truncated to its most recent assets.",
    )
    exclude_filename_patterns: list[str] = Field(
        default_factory=lambda: [
            "RingVideo_*",  # doorbell; reuses one filename across every export
            "RPReplay_Final*",  # iOS screen recording
            "Screen Recording *",  # macOS and Android screen recording
            "Screenshot*",
            "*-WA[0-9]*",  # arrived through a messaging app, was not shot here
        ],
        description="Case-insensitive glob patterns for source files a memory "
        "must never use. The holistic review already drops footage nobody "
        "chose to shoot, but it needs a model and it runs after analysis has "
        "paid for the clip; a name that settles it outright settles it for "
        "free, and keeps working with no LLM configured at all.",
    )
    exclude_stills_without_camera_exif: bool = Field(
        default=True,
        description="Drop photographs whose EXIF names no camera. Measured on a "
        "real library: of 1541 make-less stills, 1498 arrived through a "
        "messaging app and 34 were downloads, against 9 camera originals that "
        "had lost their make. Turn off for a library of exported or edited "
        "originals. Videos are exempt — a phone clip loses its make too often.",
    )
    scene_threshold: float = Field(default=27.0, ge=1.0, le=100.0)
    min_scene_duration: float = Field(default=1.0, ge=0.5, le=10.0)
    duplicate_hash_threshold: int = Field(default=8, ge=0, le=64)

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
    # 10.0 and 0.15 are what the pipeline has always actually run: these two
    # never reached the analyzer, so its constructor defaults were in force while
    # the documented 15.0/0.25 were never exercised. Wiring the config through
    # made the documented values live and moved the duration curve's peak on 32
    # of 92 clips, so the defaults were aligned to the behaviour instead.
    max_optimal_duration: float = Field(
        default=10.0,
        ge=5.0,
        le=30.0,
        description="Maximum optimal clip duration for long source videos",
    )
    target_extraction_ratio: float = Field(
        default=0.15,
        ge=0.05,
        le=0.5,
        description="Target ratio of clip to source duration (0.15 = 15% of source)",
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
        default=True,
        description="Include Live Photo video clips (3s clips from iPhone Live Photos)",
    )
    live_photo_merge_window_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Max gap between Live Photos to group into a burst cluster",
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
        # 0.3 for the same reason as the duration fields above: this never
        # reached the analyzer, so 0.3 is the gap width silence detection has
        # actually been using.
        default=0.3,
        ge=0.1,
        le=1.0,
        description="Minimum duration (seconds) of quiet audio to count as a silence gap",
    )
    min_source_short_side: int = Field(
        default=1080,
        ge=0,
        description=(
            "Clips below this short side are dropped unless they carry camera EXIF, "
            "which is how messaging re-encodes are told from genuinely old footage"
        ),
    )
    subject_policy_enabled: bool = Field(
        default=True,
        description="Prefer clips of people; ration animals and exclude object-only clips",
    )
    max_animal_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Share of a video that may be animal-only clips, so the allowance "
            "scales with length (0 disables them entirely)"
        ),
    )
    max_object_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Share of a video that may be object-only clips. They must also beat "
            "the median people clip, which is what separates a new car from a lawnmower"
        ),
    )


class HardwareAccelConfig(BaseModel):
    """Hardware acceleration settings."""

    # Auto-detect available hardware by default
    enabled: bool = Field(default=True, description="Enable hardware acceleration")

    # Encoding settings
    encoder_preset: Literal["fast", "balanced", "quality"] = Field(
        default="balanced", description="Encoder speed/quality tradeoff"
    )

    # Use GPU for frame analysis (OpenCV CUDA, etc.)
    gpu_analysis: bool = Field(
        default=True, description="Use GPU for video analysis when available"
    )

    # Decode on GPU (can speed up processing significantly)
    gpu_decode: bool = Field(default=True, description="Use hardware video decoding")


class OutputConfig(BaseModel):
    """Output settings."""

    directory: str = Field(default="~/Videos/Memories")
    format: Literal["mp4", "mov"] = "mp4"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    codec: Literal["h264", "h265", "prores"] = "h264"
    hdr_mode: HdrMode = HdrMode.AUTO
    quality: Literal["high", "medium", "low"] = "high"
    crf: int | None = Field(default=None, ge=0, le=51)

    @field_serializer("hdr_mode")
    def serialize_hdr_mode(self, value: HdrMode) -> str:
        """Serialize the HDR policy as a portable YAML/JSON string."""
        return value.value

    @property
    def effective_crf(self) -> int:
        """CRF derived from quality preset, or explicit override if set."""
        if self.crf is not None:
            return self.crf
        from immich_memories.processing.hdr_utilities import quality_to_crf

        return quality_to_crf(self.quality)

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

    # Derived-media caches. These had no limit at all: on a real library that was
    # 5.2 GB of previews and 3.5 GB of thumbnails, while the cache page reported
    # thumbnails as capped at 500 MB. 500 is kept because it is the number users
    # have already been shown -- it is now true.
    thumbnail_cache_max_size_mb: float = Field(
        default=500.0, ge=50, le=100_000, description="Maximum thumbnail cache size in MB"
    )
    preview_cache_max_size_mb: float = Field(
        default=2000.0, ge=100, le=100_000, description="Maximum clip preview cache size in MB"
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
    """Local music library used by the `music` CLI subcommand.

    Generation picks its music backend from `ace_step.enabled` / `musicgen.enabled`
    and the `--music` / `--no-music` flags; ducking and fades are fixed in the mixer.
    """

    local_music_dir: str = Field(
        default="~/Music/Memories", description="Directory for local music library"
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
        default="api",
        description="Generation mode: 'api' for remote REST server (default), 'lib' for local library (requires Python 3.12)",
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
        description=(
            "ACE-Step DiT model: 'turbo'/'base' use the 2B family; "
            "'acestep-v15-xl-turbo' is the recommended 4B XL production model"
        ),
    )
    lm_model_size: str = Field(
        default="1.7B",
        description="Language model size: '0.6B', '1.7B', or '4B'",
    )
    use_lm: bool = Field(
        default=False,
        description=(
            "Use the 5Hz language model's 'thinking mode'. Off by default: it rewrites "
            "the caption and invents its own genre metadata, which drifts instrumental "
            "prompts off-brief, and it dominates generation time (~45s -> ~17s per "
            "60s track when disabled). Enable for prompt-following on complex briefs."
        ),
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
        default=False,
        description="Show decorative line accents on title screens",
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
        default=0.1,
        ge=0.1,
        le=0.5,
        description=(
            "Lower confidence threshold for laughter/baby sounds (often quieter). "
            "At 0.2 more than half the laughter in a real library never fires an "
            "event; 0.1 raises recall from 47% to 79% and costs some precision"
        ),
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


class SpeechConfig(BaseModel):
    """Settings for speech-derived segment boundaries.

    FireRedVAD is the only detector, so there is no engine selector: `enabled:
    false` is how you turn voice activity off and fall back to PANNs-derived
    protected ranges.
    """

    enabled: bool = Field(
        default=True,
        description="Derive segment boundaries from voice activity instead of PANNs speech tags",
    )
    vad_threshold: float = Field(
        default=0.25,
        ge=0.1,
        le=0.9,
        description=(
            "Speech probability above which a frame counts as voice activity. "
            "Below FireRedVAD upstream's default "
            "of 0.4: measured across 143 library clips, 0.25 detects speech in every "
            "clip the removed Silero engine did plus 49 more, with zero false "
            "positives on clips below -40 dBFS"
        ),
    )
    min_silence_ms: int = Field(
        default=200,
        ge=50,
        le=2000,
        description=(
            "Silence needed before a speech region is closed (milliseconds). Also "
            "caps how far protected ranges are widened before boundary adjustment, "
            "so the pauses split here survive"
        ),
    )


class TranscriptionConfig(BaseModel):
    """Settings for speech transcription of candidate clips.

    Transcription is gated on voice activity, so it needs `speech.enabled`. With
    speech off there are no VAD regions, so there is no gate, so nothing is
    transcribed.
    """

    enabled: bool = Field(
        default=False,
        description="Transcribe speech in the top candidate clips",
    )
    languages: list[str] = Field(
        default_factory=list,
        description=(
            "Languages the library actually contains, e.g. ['fr', 'en']. One entry "
            "forces that language and skips detection; several restrict detection to "
            "that set. Empty means no transcription -- automatic detection across all "
            "99 languages put French audio in Japanese and in German, twice out of two"
        ),
    )
    model: str = Field(
        default="medium",
        description=(
            "ggml model name (tiny/base/small/medium/large) or a path to a model file. "
            "medium measured markedly better than base on real family audio -- base "
            "returned '- Dear.' and 'La papa.' where medium returned whole sentences -- "
            "for about 0.6s per segment. It is a ~1.5 GB first-use download"
        ),
    )
    min_voiced_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description=(
            "Voice activity required inside a clip before it is transcribed. Unmeasured "
            "starting value: VAD already returns no regions at all on roughly half a "
            "real library, which is where most of the saving comes from"
        ),
    )
    min_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Mean token probability below which a transcript is discarded. Defaults to "
            "0.0 because this signal is inverted on real audio: correct transcripts "
            "measured 0.63-0.71 and fluent nonsense 0.84-0.95, so raising the floor "
            "removes good output before bad. The voice-activity floor and the repetition "
            "guard do the filtering"
        ),
    )
    use_gpu: bool = Field(
        default=True,
        description="Use GPU when the installed whisper.cpp build supports it (Metal on macOS)",
    )


class PhotoConfig(BaseModel):
    """Photo-to-video animation settings."""

    enabled: bool = Field(default=True, description="Include photos in memory videos")
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
    burst_window_seconds: float = Field(
        default=300.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Photos this close together and visually near-identical are one burst; "
            "only the best-scored frame is kept (0 disables burst de-duplication)"
        ),
    )
    burst_hash_threshold: int = Field(
        default=8,
        ge=0,
        le=64,
        description="Perceptual-hash bits two photos may differ by and still be one burst",
    )
    moment_gap_seconds: float = Field(
        default=120.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Time window for treating a photo and a video as the same moment. "
            "A photo this close to a visually matching video is dropped."
        ),
    )
    moment_hash_threshold: int = Field(
        default=10,
        ge=0,
        le=64,
        description=(
            "Perceptual-hash bits a photo may differ from a nearby video and "
            "still count as the same scene (0 = only identical framing)"
        ),
    )
    score_penalty: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Score reduction for photos vs videos (0.2 = photos score 80% of videos)",
    )


class AutomationConfig(BaseModel):
    """Smart automation settings for candidate detection and auto-generation."""

    enabled: bool = Field(
        default=False,
        description="Run the daily auto-run decision inside the UI/Docker process",
    )
    daily_at: str = Field(
        default="09:00",
        description="Local wall-clock time (HH:MM) for the in-process daily run",
    )
    cooldown_hours: int = Field(default=24, ge=1, le=168)
    max_delivery_attempts: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Give up on an Immich upload after this many failed attempts",
    )
    upload_to_immich: bool = Field(default=False)
    album_name: str | None = Field(default=None)
    detect_monthly: bool = Field(default=True)
    detect_yearly: bool = Field(default=True)
    detect_trips: bool = Field(default=True)
    detect_person_spotlight: bool = Field(default=True)
    detect_activity_burst: bool = Field(default=True)
    burst_threshold: float = Field(default=2.0, ge=1.0, le=10.0)

    @field_validator("daily_at")
    @classmethod
    def _normalize_daily_at(cls, value: str) -> str:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError("daily_at must be a 24h wall-clock time like '09:00'")
        return f"{int(match[1]):02d}:{match[2]}"


class NotificationConfig(BaseModel):
    """Apprise notification settings for job completion alerts."""

    enabled: bool = Field(default=False)
    urls: list[str] = Field(default_factory=list, description="Apprise notification URLs")
    on_success: bool = Field(default=True)
    on_failure: bool = Field(default=True)
    attach_thumbnail: bool = Field(
        default=False,
        description="Attach a generated video thumbnail to successful notifications",
    )
    cooldown_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Suppress normal delivery attempts after a notification failure",
    )


class UploadConfig(BaseModel):
    """Upload generated videos back to Immich."""

    enabled: bool = Field(default=False, description="Upload generated video to Immich")
    album_name: str | None = Field(
        default=None, description="Album name (created if missing, reused if exists)"
    )
