"""Configuration models for the music under a memory.

A local library to pick from, or one of the two generators that write a track
for the video instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars


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
