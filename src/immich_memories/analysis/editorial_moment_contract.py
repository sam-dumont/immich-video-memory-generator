"""The moment and card shapes the editorial case hands to the moment wall."""

from __future__ import annotations

from dataclasses import dataclass

from immich_memories.analysis.selection_source_groups import EditorialGroup

__all__ = [
    "Description",
    "ModelAnswer",
    "Moment",
    "MomentCard",
]


@dataclass(frozen=True)
class Description:
    asset_id: str
    text: str


@dataclass(frozen=True)
class ModelAnswer:
    prompt: str
    raw: str
    wall_seconds: float


@dataclass(frozen=True)
class Moment:
    alias: str
    group: EditorialGroup
    descriptions: tuple[Description, ...]


@dataclass(frozen=True)
class MomentCard:
    moment: Moment
    summary: str
    answer: ModelAnswer | None
