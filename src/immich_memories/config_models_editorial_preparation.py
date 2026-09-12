"""Providers for the exact public annotation generation used by editorial selection."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars

PreparationTier = Literal["full", "no_captions", "metadata_only"]
"""Which producers a deployment demands. Named, never inferred from what happens to fail."""


class EditorialPreparationConfig(BaseModel):
    """Missing facts are acquired; complete facts never contact a provider."""

    tier: PreparationTier = "full"
    caption_base_url: str = "http://localhost:8092/v1"
    caption_timeout_seconds: float = Field(default=90, gt=0)
    caption_concurrency: int = Field(default=4, ge=1, le=16)
    batch_size: int = Field(default=32, ge=1, le=256)
    head_bundle: str = Field(
        default="", description="Blank uses the packaged public six-head bundle"
    )
    detector_python: str = Field(
        default="", description="Blank uses the current Python interpreter"
    )
    detector_cache_dir: str = Field(default="", description="Blank uses the Hugging Face cache")
    allow_model_downloads: bool = False

    @field_validator("head_bundle", "detector_python", "detector_cache_dir", mode="before")
    @classmethod
    def expand_paths(cls, value: object) -> object:
        return expand_env_vars(value) if isinstance(value, str) else value

    @field_validator("caption_base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        value = value.rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("caption_base_url must be an HTTP(S) endpoint without credentials")
        return value

    @property
    def demands_captions(self) -> bool:
        return self.tier == "full"

    @property
    def demands_models(self) -> bool:
        """Whether the ONNX encoder, the six heads and the two detectors are asked for."""
        return self.tier in {"full", "no_captions"}

    @property
    def head_bundle_path(self) -> Path:
        if self.head_bundle.strip():
            return Path(self.head_bundle).expanduser()
        return Path(__file__).parent / "triage" / "bundled_heads" / "public-6heads-v3.npz"
