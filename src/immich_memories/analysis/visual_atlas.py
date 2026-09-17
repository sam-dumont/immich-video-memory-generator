"""Local visual evidence that can be reused across editorial contact sheets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal


@dataclass(frozen=True)
class AtlasSource:
    """One source asset and the locally available pixels that represent it."""

    asset: Any
    preview_jpeg: bytes | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class AtlasTile:
    """One stable visual tile, including an explicit unavailable state."""

    entity_id: str
    kind: Literal["photo", "filmstrip", "unavailable"]
    jpeg_bytes: bytes | None
    sha256: str | None
    frame_count: int
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class VisualAtlas:
    """Chronological tiles that derived contact sheets can reuse without new reads."""

    tiles: tuple[AtlasTile, ...]
    _tiles_by_id: Mapping[str, AtlasTile] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Preserve the historical first-match behavior if a hand-built test atlas
        # contains a duplicate ID. Production construction rejects duplicates.
        by_id: dict[str, AtlasTile] = {}
        for tile in self.tiles:
            by_id.setdefault(tile.entity_id, tile)
        object.__setattr__(self, "_tiles_by_id", MappingProxyType(by_id))

    def tile_for(self, entity_id: str) -> AtlasTile:
        """Return the tile for one source entity."""
        return self._tiles_by_id[entity_id]
