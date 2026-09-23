"""Configuration for the store-backed production editorial planner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from immich_memories.analysis.editorial_description_contract import DESCRIPTION_MODEL
from immich_memories.config_models import expand_env_vars
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig

logger = logging.getLogger(__name__)

# Heads that once shipped and no longer do. A config file that still names one is a
# config written before the head was retired, not a broken config: the name is dropped
# and the run continues, because nothing left in the tree reads it.
RETIRED_HEADS = frozenset({"swim"})


def _default_head_versions() -> dict[str, str]:
    """Return the producer set used by the public 2022 annotation store."""
    return {
        "activity": "public-v1",
        "children": "public-v1",
        "doc_docling": "det-v2",
        "frame_kind": "public-v1",
        "location": "public-v1",
        "nsfw_marqo": "det-v3",
        "people": "public-v1",
        "screen": "public-v1-strict",
        "uncovered_person": "public-v1",
        "venue": "oi-v3",
    }


class EditorialConfig(BaseModel):
    """Versioned evidence for the production story-first selector.

    Old ``enabled`` and ``story_first`` keys are ignored when loading existing
    configurations. Selection no longer has an alternate route.
    """

    preparation: EditorialPreparationConfig = Field(default_factory=EditorialPreparationConfig)
    reader: Literal["auto", "model", "rules"] = "auto"
    thin_model_layer: bool = Field(
        default=True,
        description=(
            "Build the cut with the no-model reader and let the model polish it, instead of "
            "planning the whole film with the model. Needs a catalogued period; without one the "
            "run plans the film with the story-first planner. False makes the model plan the "
            "whole film even when an account exists"
        ),
    )

    def resolve_reader(self, model: str) -> Literal["model", "rules"]:
        """A blank model selects the bounded rules reader unless explicitly required."""
        if self.reader == "rules" or self.reader == "auto" and not model.strip():
            return "rules"
        if not model.strip():
            raise ValueError("editorial runtime needs a nonblank LLM model")
        return "model"

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
        # Saved defaults must request the current producer after an upgrade.
        for head in ("doc_docling", "nsfw_marqo"):
            if value.get(head) == "det-v1":
                value = value | {head: "det-v2"}
        # The exposure head now reads a video on eight frames, so a det-v2 bank owes it
        # a fresh answer for every source. Docling still reads the one preview.
        if value.get("nsfw_marqo") == "det-v2":
            value = value | {"nsfw_marqo": "det-v3"}
        retired = sorted(RETIRED_HEADS & set(value))
        if retired:
            logger.info("ignoring retired head versions: %s", ", ".join(retired))
            value = {head: version for head, version in value.items() if head not in RETIRED_HEADS}
        return value

    @property
    def annotation_database_path(self) -> Path | None:
        """Return an explicit override; resolve the cache-relative default at runtime."""
        raw = self.annotation_database.strip()
        return Path(raw).expanduser() if raw else None

    def resolve_annotation_database(self, cache_path: Path) -> Path:
        """Use the product store without consulting private evaluation datasets."""
        return self.annotation_database_path or cache_path / "annotations.sqlite"
