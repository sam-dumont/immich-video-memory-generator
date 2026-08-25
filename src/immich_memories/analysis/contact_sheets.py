"""One encoded, numbered contact-sheet format for every editorial pass."""

from __future__ import annotations

import io
import math
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar

MAX_SHEET_TILES = 120
MAX_SHEET_PX = 2100
LAYOUT_VERSION = "1"
_TARGET_ASPECT = 1.6
_MAX_TILE = 400
_MIN_TILE = 120
_SHEET_BACKGROUND = (18, 18, 18)

T = TypeVar("T")


@dataclass(frozen=True)
class TileRef:
    """The stable entity represented by one global tile number."""

    number: int
    entity_id: str


@dataclass(frozen=True)
class ContactSheetPage:
    """The exact JPEG evidence that is both stored and attached to a request."""

    sheet_id: str
    path: Path
    jpeg_bytes: bytes
    sha256: str
    tile_refs: tuple[TileRef, ...]
    layout_version: str


def sheet_layout(count: int) -> tuple[int, int]:
    """Return the wide grid dimensions for one page of visual evidence."""
    columns = max(1, round(math.sqrt(count * _TARGET_ASPECT)))
    tile = min(_MAX_TILE, MAX_SHEET_PX // columns)
    rows = -(-count // columns)
    if rows * tile > MAX_SHEET_PX * 1.2:
        tile = int(MAX_SHEET_PX * 1.2) // rows
    return columns, max(_MIN_TILE, tile)


def sheets_of(items: list[T], per_sheet: int = MAX_SHEET_TILES) -> list[list[tuple[int, T]]]:
    """Split items into bounded pages while keeping tile numbers global."""
    return [
        [
            (offset + number + 1, item)
            for number, item in enumerate(items[offset : offset + per_sheet])
        ]
        for offset in range(0, len(items), per_sheet)
    ]


def tile_sheet(frames: list[tuple[int, Any | None]]):
    """Lay images in a numbered wide grid without dropping unavailable entries."""
    from PIL import Image, ImageDraw

    if not frames:
        return None
    columns, tile = sheet_layout(len(frames))
    rows = -(-len(frames) // columns)
    sheet = Image.new("RGB", (columns * tile, rows * tile), _SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    label = max(16, tile // 11)
    for position, (number, frame) in enumerate(frames):
        left = (position % columns) * tile
        top = (position // columns) * tile
        if frame is not None:
            thumbnail = frame.copy()
            thumbnail.thumbnail((tile - 6, tile - 6))
            sheet.paste(thumbnail, (left + 3, top + 3))
        _draw_number(draw, left, top, label, number)
    return sheet


def build_contact_sheets(
    tiles: tuple[Any, ...] | list[Any],
    scope_id: str,
    output_dir: Path,
    *,
    per_sheet: int = MAX_SHEET_TILES,
) -> tuple[ContactSheetPage, ...]:
    """Encode each page once, write those exact bytes, and retain its stable mapping."""
    entries = list(tiles)
    entity_ids = [getattr(tile, "entity_id", None) for tile in entries]
    if any(not isinstance(entity_id, str) or not entity_id for entity_id in entity_ids):
        raise ValueError("contact sheet tiles need non-empty entity IDs")
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("contact sheet tiles must have unique entity IDs")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create contact sheet directory: {output_dir}") from exc

    pages: list[ContactSheetPage] = []
    for page_index, numbered in enumerate(sheets_of(entries, per_sheet), start=1):
        frames = [
            (number, _image_for(tile, tile_size=sheet_layout(len(numbered))[1]))
            for number, tile in numbered
        ]
        sheet = tile_sheet(frames)
        assert sheet is not None
        jpeg_bytes = _encode_jpeg(sheet)
        sheet_id = f"{scope_id}-{page_index:03d}"
        path = output_dir / f"{sheet_id}.jpg"
        path.write_bytes(jpeg_bytes)
        pages.append(
            ContactSheetPage(
                sheet_id=sheet_id,
                path=path,
                jpeg_bytes=jpeg_bytes,
                sha256=sha256(jpeg_bytes).hexdigest(),
                tile_refs=tuple(TileRef(number, tile.entity_id) for number, tile in numbered),
                layout_version=LAYOUT_VERSION,
            )
        )
    return tuple(pages)


def _image_for(tile: Any, *, tile_size: int):
    from PIL import Image, ImageDraw

    data = getattr(tile, "jpeg_bytes", None)
    if isinstance(data, bytes):
        with suppress(OSError), Image.open(io.BytesIO(data)) as decoded:
            return decoded.convert("RGB")
    image = Image.new("RGB", (tile_size - 6, tile_size - 6), (35, 35, 35))
    reason = getattr(tile, "unavailable_reason", None) or "unavailable"
    ImageDraw.Draw(image).text((8, 8), reason, fill=(220, 220, 220))
    return image


def _encode_jpeg(sheet: Any) -> bytes:
    buffer = io.BytesIO()
    sheet.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def _draw_number(draw: Any, left: int, top: int, label: int, number: int) -> None:
    text = str(number)
    draw.rectangle(
        [left + 3, top + 3, left + 3 + label * (0.6 * len(text) + 0.8), top + 3 + label],
        fill=(0, 0, 0),
    )
    draw.text((left + 7, top + 5), text, fill=(255, 255, 255))
