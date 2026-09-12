"""Service settings, read from ``IMMICH_MEMORIES_INFERENCE_*``.

immich-machine-learning reads ``MACHINE_LEARNING_*`` the same way; this is the
one place a deployment changes what the service does, so nothing here reaches a
producer key (§4: device and location are operational, never identity).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig

ENV_PREFIX = "IMMICH_MEMORIES_INFERENCE_"


class InferenceSettings(BaseSettings):
    """Where the weights live, which provider to ask for, and how long to hold them."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    # Loopback by default: the container publishes the port, an unconfigured
    # laptop run does not put a model server on the LAN.
    host: str = "127.0.0.1"
    port: int = Field(default=8092, ge=1, le=65535)
    cache_dir: Path = Path("/cache")
    encoder: Path | None = None
    marqo_onnx: Path | None = None
    bundle: Path | None = None
    provider: Literal["auto", "cpu", "cuda", "coreml"] = "auto"
    request_threads: int = Field(default=4, ge=1, le=64)
    # The weights go, the process stays. immich-ml sends itself SIGINT after its
    # TTL; a restart loop on a NAS costs more than a resident idle process.
    idle_unload_seconds: float = Field(default=300.0, ge=0)
    preload: bool = False
    detector_cache_dir: Path | None = None
    allow_model_downloads: bool = False
    max_image_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)

    @property
    def encoder_path(self) -> Path:
        return (self.encoder or self.cache_dir / "dinov2-small.onnx").expanduser()

    @property
    def marqo_onnx_path(self) -> Path:
        return (self.marqo_onnx or self.cache_dir / "nsfw-marqo-384.onnx").expanduser()

    @property
    def bundle_path(self) -> Path:
        # The app's own answer to "where is the packaged bundle", so the service
        # cannot drift to a different one and re-key every head fact.
        return (self.bundle or EditorialPreparationConfig().head_bundle_path).expanduser()

    @property
    def detector_cache(self) -> str | None:
        return str(self.detector_cache_dir.expanduser()) if self.detector_cache_dir else None
