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


class EditorialPeopleConfig(BaseModel):
    """How the people file's close family (partner, child, parent) reach the selection."""

    seat_min_pictures: int = Field(
        default=20,
        ge=1,
        description=(
            "A close family member on at least this many of the period's pictures, and in none "
            "of its shots, gets one seat in the film"
        ),
    )
    seat_min_share: float = Field(
        default=0.05,
        gt=0,
        le=1,
        description="Or on at least this share of the period's pictures, however few that is",
    )
    # Both "big story" numbers were measured with the rules reader on six real months
    # (2026-09-23, every story of each month). Pictures per photographed day, against the
    # month's median photographed day: ordinary stories, multi-day weeks included, all sat at
    # or under 1.8; dense occasions at 2.1 to 10. The share of a story's pictures naming a
    # partner, child or parent: dense stories of strangers (two city race days, a dense day
    # among relatives who are not close family) sat at 0 to 6 %; dense close-family occasions
    # at 39 to 73 %. Each default sits in its measured gap.
    big_story_density: float = Field(
        default=2.0,
        gt=0,
        description=(
            "A story without three favourites is floored to major only when its pictures per "
            "photographed day reach this multiple of the period's median photographed day..."
        ),
    )
    big_story_family_share: float = Field(
        default=0.3,
        ge=0,
        le=1,
        description="...and at least this share of its pictures show a partner, child or parent",
    )


class EditorialConfig(BaseModel):
    """Versioned evidence for the production story-first selector.

    Old ``enabled`` and ``story_first`` keys are ignored when loading existing
    configurations. Selection no longer has an alternate route.
    """

    preparation: EditorialPreparationConfig = Field(default_factory=EditorialPreparationConfig)
    people: EditorialPeopleConfig = Field(default_factory=EditorialPeopleConfig)
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

    thin_batched_audience: bool = Field(
        default=False,
        description=(
            "Ask the thin layer's audience question of twelve carriers per request, in two row "
            "orders, instead of one carrier per request. Every carrier gets its own answer, one "
            "either order holds is held, and one the replies skip is asked alone. Off until a "
            "probe on the local reader shows batching keeps every hold"
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
