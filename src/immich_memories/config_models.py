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


def has_unresolved_env_reference(value: str) -> bool:
    """Whether a `${VAR}` survived expansion, meaning the variable is not set."""
    return _ENV_REFERENCE.search(value) is not None


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

    # `auto` walks NVIDIA, Apple, QSV, VAAPI and takes the first that can encode.
    # Naming one probes that one only, which is how a measurement says which chip
    # it ran on: a host where the named backend cannot encode falls to software
    # and says so, instead of publishing another chip's numbers under its name.
    backend: Literal["auto", "none", "nvidia", "apple", "vaapi", "qsv"] = Field(
        default="auto", description="Which encode backend the render probes"
    )

    # Encoding settings
    encoder_preset: Literal["fast", "balanced", "quality"] = Field(
        default="balanced", description="Encoder speed/quality tradeoff"
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
    # thumbnails as capped at 500 MB.
    #
    # 500 MB was sized for the per-clip scorer, which only ever fetched the clips
    # it had already selected. The story-first route annotates every candidate in
    # scope instead, so this budget is a function of library size: measured at
    # 315 KB per Immich preview, one memory's 10,793-candidate scope wants 3.4 GB
    # and one real cache held 12,159 previews for 3.92 GB. At 500 MB every run
    # evicted the previous run's previews, and the next overlapping memory
    # re-downloaded them and re-captioned the assets whose banked caption failure
    # no longer validated. 10 GB holds roughly 31,000 previews -- three scopes of
    # that size -- and matches what video_cache_max_size_gb already treats as an
    # acceptable cache footprint. A bigger library gets a warning naming this key.
    #
    # It is the full Immich preview on purpose, not a smaller derived tile, even
    # though nothing consumes 1440 px: the preview bytes ARE the contact-sheet
    # image and their sha256 is that sheet's identity, the DINOv2 transform wants
    # a 256 px short side that a 400 px caption tile does not have on 16:9, and
    # the duplicate-hash bank and banked caption failures are both keyed on them.
    # Shrinking the rendition is a re-grade, not a config change.
    thumbnail_cache_max_size_mb: float = Field(
        default=10_000.0, ge=50, le=100_000, description="Maximum thumbnail cache size in MB"
    )
    # Not library-sized: preview-cache/ holds the video renditions the wizard's
    # player streams, so its working set is the clips of one cut -- tens of files,
    # not one per candidate -- and 2 GB stays a plain cap.
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
