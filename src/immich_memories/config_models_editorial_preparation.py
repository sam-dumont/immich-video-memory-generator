"""Providers for the exact public annotation generation used by editorial selection."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from immich_memories.config_models import expand_env_vars, has_unresolved_env_reference

# The one place the sensitive-content export's host is named; `models fetch`
# verifies the digest pinned in analysis/editorial_preparation_detectors.py
# whatever this points at.
MARQO_ONNX_URL = (
    "https://github.com/sam-dumont/immich-video-memory-generator/"
    "releases/download/models-v1/nsfw-marqo-384-924658f1.onnx"
)

PreparationTier = Literal["full", "no_captions", "metadata_only"]
"""Which producers a deployment demands. Named, never inferred from what happens to fail."""


def _endpoint(value: str, field: str) -> str:
    value = value.rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError(f"{field} must be an HTTP(S) endpoint without credentials")
    return value


class PictureFactsConfig(BaseModel):
    """A local typed-decision reader, asked once per picture at ingest.

    On, because what it answers is worth having and it is told exactly one endpoint: the
    address below and nothing else, which is a local one. A deployment with nothing there
    pays one line in the preparation report and cuts its films as before.
    """

    enabled: bool = True
    base_url: str = "http://127.0.0.1:8080/v1"
    timeout_seconds: float = Field(default=120, gt=0)
    concurrency: int = Field(
        default=1,
        ge=1,
        le=16,
        description=(
            "Picture reads in flight. The reader answers one tile at a time on one GPU; "
            "raise it only for a server that batches across cards"
        ),
    )

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _endpoint(value, "picture_facts.base_url")


class EditorialPreparationConfig(BaseModel):
    """Missing facts are acquired; complete facts never contact a provider."""

    tier: PreparationTier = "full"
    picture_facts: PictureFactsConfig = Field(default_factory=PictureFactsConfig)
    caption_base_url: str = "http://localhost:8092/v1"
    caption_artifact_id: str = Field(
        default="",
        max_length=512,
        description="Declared caption model artifact/revision, recorded for new rows; does not re-caption existing rows",
    )
    caption_api_key: str = Field(
        default="",
        description="Bearer token for a caption server that wants one; never taken from llm",
    )
    caption_timeout_seconds: float = Field(default=90, gt=0)
    caption_concurrency: int = Field(
        default=1,
        ge=1,
        le=16,
        description=(
            "Caption requests in flight. One is right for any CPU captioner; raise it "
            "for a server on a GPU"
        ),
    )
    batch_size: int = Field(default=32, ge=1, le=256)
    head_bundle: str = Field(
        default="", description="Blank uses the packaged public six-head bundle"
    )
    detector_python: str = Field(
        default="", description="Blank uses the current Python interpreter"
    )
    detector_cache_dir: str = Field(default="", description="Blank uses the Hugging Face cache")
    marqo_onnx: str = Field(
        default="~/.immich-memories/models/detectors/nsfw-marqo-384.onnx",
        description=(
            "ONNX export of the pinned sensitive-content detector (22.5 MB, digest-pinned in "
            "code); `models fetch` downloads it here"
        ),
    )
    marqo_onnx_url: str = Field(
        default=MARQO_ONNX_URL,
        description="Where `models fetch` downloads the pinned sensitive-content export from",
    )
    allow_model_downloads: bool = False

    @field_validator(
        "head_bundle", "detector_python", "detector_cache_dir", "marqo_onnx", mode="before"
    )
    @classmethod
    def expand_paths(cls, value: object) -> object:
        return expand_env_vars(value) if isinstance(value, str) else value

    @field_validator("caption_api_key", mode="before")
    @classmethod
    def resolve_caption_key(cls, value: object) -> object:
        """A `${VAR}` whose variable is unset means no key, never a literal to send as one.

        A path can survive being written out verbatim. A bearer header cannot:
        `Authorization: Bearer ${OPENAI_API_KEY}` earns a 401 that reads as a wrong
        key, when the real cause is a variable nobody exported.
        """
        if not isinstance(value, str):
            return value
        expanded = expand_env_vars(value)
        return "" if has_unresolved_env_reference(expanded) else expanded

    @field_validator("caption_base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _endpoint(value, "caption_base_url")

    @property
    def marqo_onnx_path(self) -> Path:
        return Path(self.marqo_onnx).expanduser()

    @property
    def demands_captions(self) -> bool:
        return self.tier == "full"

    @property
    def demands_picture_facts(self) -> bool:
        """Never implied by a tier: reading pixels twice is a deployment's own decision."""
        return self.picture_facts.enabled

    @property
    def demands_models(self) -> bool:
        """Whether the ONNX encoder, the eight heads and the two detectors are asked for."""
        return self.tier in {"full", "no_captions"}

    @property
    def head_bundle_path(self) -> Path:
        if self.head_bundle.strip():
            return Path(self.head_bundle).expanduser()
        return Path(__file__).parent / "triage" / "bundled_heads" / "public-8heads-v4.npz"
