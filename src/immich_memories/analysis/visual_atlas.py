"""Local visual evidence that can be reused across editorial contact sheets."""

from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from immich_memories.analysis.thumbnail_prefetch import cached_preview_bytes
from immich_memories.processing.frame_sampling import probe_duration, sample_segment_frames

FILMSTRIP_FRAME_COUNT = 3
FILMSTRIP_WIDTH = 360


@dataclass(frozen=True)
class AtlasSource:
    """One source asset and the locally available pixels that represent it."""

    asset: Any
    preview_jpeg: bytes | None = None
    motion_path: Path | None = None
    proposed_segment: tuple[float, float] | None = None
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

    def tile_for(self, entity_id: str) -> AtlasTile:
        """Return the tile for one source entity."""
        for tile in self.tiles:
            if tile.entity_id == entity_id:
                return tile
        raise KeyError(entity_id)


def build_visual_atlas(
    sources: tuple[AtlasSource, ...] | list[AtlasSource],
    *,
    frame_cache_dir: Path | None,
    thumbnail_cache: Any | None = None,
) -> VisualAtlas:
    """Build one local visual tile per source, preserving input order exactly."""
    entity_ids = tuple(_entity_id(source) for source in sources)
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("visual atlas sources must have unique entity IDs")
    atlas = VisualAtlas(
        tuple(_tile_for(source, frame_cache_dir, thumbnail_cache) for source in sources)
    )
    for entity_id in entity_ids:
        atlas.tile_for(entity_id)
    return atlas


def _entity_id(source: AtlasSource) -> str:
    entity_id = getattr(source.asset, "id", None)
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("visual atlas sources need a non-empty asset ID")
    return entity_id


def _tile_for(
    source: AtlasSource, frame_cache_dir: Path | None, thumbnail_cache: Any | None
) -> AtlasTile:
    entity_id = _entity_id(source)
    if _has_motion(source):
        filmstrip = _filmstrip_for(source, frame_cache_dir)
        if filmstrip is not None:
            return AtlasTile(
                entity_id=entity_id,
                kind="filmstrip",
                jpeg_bytes=filmstrip[0],
                sha256=sha256(filmstrip[0]).hexdigest(),
                frame_count=filmstrip[1],
            )
    preview_jpeg = source.preview_jpeg or _cached_preview(thumbnail_cache, entity_id)
    if preview_jpeg is not None and _is_jpeg(preview_jpeg):
        return AtlasTile(
            entity_id=entity_id,
            kind="photo",
            jpeg_bytes=preview_jpeg,
            sha256=sha256(preview_jpeg).hexdigest(),
            frame_count=1,
        )
    return AtlasTile(
        entity_id=entity_id,
        kind="unavailable",
        jpeg_bytes=None,
        sha256=None,
        frame_count=0,
        unavailable_reason=(
            source.unavailable_reason or "no usable local preview or motion frames"
        ),
    )


def _cached_preview(thumbnail_cache: Any | None, entity_id: str) -> bytes | None:
    if thumbnail_cache is None:
        return None
    return cached_preview_bytes(thumbnail_cache, entity_id)


def _has_motion(source: AtlasSource) -> bool:
    return source.motion_path is not None and bool(
        getattr(source.asset, "is_video", False) or getattr(source.asset, "is_live_photo", False)
    )


def _filmstrip_for(source: AtlasSource, frame_cache_dir: Path | None) -> tuple[bytes, int] | None:
    assert source.motion_path is not None
    start, end = source.proposed_segment or (0.0, probe_duration(source.motion_path))
    if end <= start:
        return None
    frame_paths = sample_segment_frames(
        source.motion_path,
        start_time=start,
        end_time=end,
        count=FILMSTRIP_FRAME_COUNT,
        width=FILMSTRIP_WIDTH,
        cache_dir=frame_cache_dir,
    )
    frames = [_open_jpeg(path) for path in frame_paths]
    usable = [frame for frame in frames if frame is not None]
    if not usable:
        return None
    return _compose_filmstrip(usable), len(usable)


def _open_jpeg(path: Path):
    from PIL import Image

    try:
        with Image.open(path) as image:
            return image.convert("RGB")
    except OSError:
        return None


def _is_jpeg(data: bytes) -> bool:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except OSError:
        return False
    return True


def _compose_filmstrip(frames: list[Any]) -> bytes:
    from PIL import Image, ImageOps

    panel = FILMSTRIP_WIDTH // len(frames)
    strip = Image.new("RGB", (FILMSTRIP_WIDTH, panel), (18, 18, 18))
    for index, frame in enumerate(frames):
        strip.paste(ImageOps.fit(frame, (panel, panel)), (index * panel, 0))
    buffer = io.BytesIO()
    strip.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()
