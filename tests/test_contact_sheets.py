"""Encoded contact sheets preserve their exact evidence bytes and references."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from immich_memories.analysis.visual_atlas import AtlasTile


def _tile(entity_id: str, colour: str) -> AtlasTile:
    image = Image.new("RGB", (32, 24), colour)
    buffer = BytesIO()
    image.save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    return AtlasTile(entity_id, "photo", jpeg, sha256(jpeg).hexdigest(), 1)


def test_contact_sheet_writes_and_exposes_the_same_encoded_jpeg_bytes(tmp_path: Path) -> None:
    """Disk, hash, attachment bytes, and chronological tile references cannot drift."""
    from immich_memories.analysis.contact_sheets import TileRef, build_contact_sheets

    tiles = tuple(
        _tile(f"asset-{number}", colour) for number, colour in enumerate(("red", "green"))
    )
    page = build_contact_sheets(tiles, scope_id="episode-1", output_dir=tmp_path)[0]

    assert page.path.read_bytes() == page.jpeg_bytes
    assert sha256(page.jpeg_bytes).hexdigest() == page.sha256
    assert page.tile_refs == tuple(TileRef(i + 1, tile.entity_id) for i, tile in enumerate(tiles))


def test_contact_sheets_preserve_an_unavailable_asset_as_a_numbered_tile(tmp_path: Path) -> None:
    """Missing pixels must not shift the number of every later visual decision."""
    from immich_memories.analysis.contact_sheets import build_contact_sheets

    tiles = (
        _tile("before", "red"),
        AtlasTile("unavailable", "unavailable", None, None, 0, "no thumbnail"),
        _tile("after", "blue"),
    )

    page = build_contact_sheets(tiles, scope_id="episode-1", output_dir=tmp_path)[0]

    assert [ref.entity_id for ref in page.tile_refs] == ["before", "unavailable", "after"]


def test_contact_sheets_reject_duplicate_entity_ids(tmp_path: Path) -> None:
    """A numbered tile can point at exactly one stable entity."""
    from immich_memories.analysis.contact_sheets import build_contact_sheets

    with pytest.raises(ValueError, match="unique entity IDs"):
        build_contact_sheets((_tile("same", "red"), _tile("same", "blue")), "scope", tmp_path)
