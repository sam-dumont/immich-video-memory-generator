"""Configuration for the store-backed production editorial planner."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from immich_memories.analysis.editorial_description_contract import DESCRIPTION_MODEL
from immich_memories.config_models import expand_env_vars
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig


def _default_head_versions() -> dict[str, str]:
    """Return the producer set used by the public 2022 annotation store."""
    return {
        "activity": "public-v1",
        "children": "public-v1",
        "doc_docling": "det-v1",
        "location": "public-v1",
        "nsfw_marqo": "det-v2",
        "people": "public-v1",
        "swim": "oi-v3",
        "venue": "oi-v3",
    }


class EditorialConfig(BaseModel):
    """Versioned evidence for the production story-first selector.

    Old ``enabled`` and ``story_first`` keys are ignored when loading existing
    configurations. Selection no longer has an alternate route.
    """

    preparation: EditorialPreparationConfig = Field(default_factory=EditorialPreparationConfig)
    annotation_database: str = Field(
        default="",
        description=(
            "SQLite annotation facts and editorial banks; defaults to annotations.sqlite "
            "inside the configured cache directory"
        ),
    )
    description_model: str = Field(
        default=DESCRIPTION_MODEL,
        description="Exact producer of descriptions and description fields",
    )
    head_versions: dict[str, str] = Field(
        default_factory=_default_head_versions,
        description="Exact producer version selected for each annotation head",
    )
    pixel_producer_key: str = Field(
        default="pixel-facts-v1",
        description="Exact producer of pixel facts and thresholds",
    )

    @field_validator("annotation_database", mode="before")
    @classmethod
    def expand_database_environment(cls, value: object) -> object:
        """Expand only the project's explicit `${NAME}` configuration form."""
        return expand_env_vars(value) if isinstance(value, str) else value

    @field_validator("description_model", "pixel_producer_key")
    @classmethod
    def require_scalar_producer(cls, value: str) -> str:
        """Reject cache identities that could collapse unrelated producers."""
        if not value.strip():
            raise ValueError("editorial producer versions cannot be blank")
        return value

    @field_validator("head_versions")
    @classmethod
    def require_head_producers(cls, value: dict[str, str]) -> dict[str, str]:
        """Every configured head must name one exact, nonblank producer."""
        if not value or any(
            not name.strip() or not version.strip() for name, version in value.items()
        ):
            raise ValueError("editorial head versions must be nonblank")
        return value

    @property
    def annotation_database_path(self) -> Path | None:
        """Return an explicit override; resolve the cache-relative default at runtime."""
        raw = self.annotation_database.strip()
        return Path(raw).expanduser() if raw else None

    def resolve_annotation_database(self, cache_path: Path) -> Path:
        """Use the product store without consulting private evaluation datasets."""
        return self.annotation_database_path or cache_path / "annotations.sqlite"
