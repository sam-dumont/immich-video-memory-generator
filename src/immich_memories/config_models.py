"""Configuration models for the resources a run uses.

The Immich server it reads from, the local cache it writes through, and the
hardware it encodes on. `expand_env_vars` lives here too: every config module
that holds a credential needs it.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from immich_memories.api.compatibility import ApiVersionPolicy

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
