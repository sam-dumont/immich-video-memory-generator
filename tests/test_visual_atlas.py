"""The reusable visual atlas turns local visual evidence into stable tiles."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image


def _jpeg(path: Path, colour: str) -> Path:
    Image.new("RGB", (48, 32), colour).save(path, "JPEG")
    return path


def test_video_tile_is_one_locally_composed_chronological_filmstrip(tmp_path: Path) -> None:
    """A video consumes one tile even though its locally decoded frames show motion."""
    from immich_memories.analysis.visual_atlas import AtlasSource, build_visual_atlas

    frames = tuple(_jpeg(tmp_path / f"{colour}.jpg", colour) for colour in ("red", "green", "blue"))
    source = AtlasSource(
        asset=SimpleNamespace(id="video-1", is_video=True),
        motion_path=tmp_path / "video.mp4",
        proposed_segment=(0.0, 3.0),
    )

    # WHY: frame decoding is the FFmpeg boundary; this test specifies atlas composition only.
    with patch(
        "immich_memories.analysis.visual_atlas.sample_segment_frames", return_value=frames
    ) as sample:
        atlas = build_visual_atlas((source,), frame_cache_dir=tmp_path / "frames")

    tile = atlas.tile_for("video-1")
    assert sample.call_count == 1
    assert tile.kind == "filmstrip"
    assert tile.frame_count == 3
    assert tile.sha256 == sha256(tile.jpeg_bytes).hexdigest()


def test_live_photo_motion_is_one_locally_composed_chronological_filmstrip(
    tmp_path: Path,
) -> None:
    """A Live Photo uses its local motion companion even though it is not a video asset."""
    from immich_memories.analysis.visual_atlas import AtlasSource, build_visual_atlas

    frames = tuple(
        _jpeg(tmp_path / f"live-{colour}.jpg", colour) for colour in ("red", "green", "blue")
    )
    motion_path = tmp_path / "live-photo.mov"
    motion_path.write_bytes(b"motion")
    source = AtlasSource(
        asset=SimpleNamespace(
            id="live-photo-1",
            is_video=False,
            is_live_photo=True,
            live_photo_video_id="motion-asset-1",
        ),
        motion_path=motion_path,
        proposed_segment=(0.0, 3.0),
    )

    # WHY: frame decoding is the FFmpeg boundary; this test specifies atlas composition only.
    with patch(
        "immich_memories.analysis.visual_atlas.sample_segment_frames", return_value=frames
    ) as sample:
        atlas = build_visual_atlas((source,), frame_cache_dir=tmp_path / "frames")

    tile = atlas.tile_for("live-photo-1")
    assert sample.call_count == 1
    assert tile.kind == "filmstrip"
    assert tile.frame_count == 3


def test_photo_tile_reuses_a_cached_preview_without_a_requester(tmp_path: Path) -> None:
    """Photo evidence is loaded from the local thumbnail cache, never fetched by the atlas."""
    from immich_memories.analysis.visual_atlas import AtlasSource, build_visual_atlas
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

    preview = _jpeg(tmp_path / "preview.jpg", "purple").read_bytes()
    cache = ThumbnailCache(tmp_path / "thumbnails")
    cache.put("photo-1", "preview", preview)

    atlas = build_visual_atlas(
        (AtlasSource(asset=SimpleNamespace(id="photo-1", is_video=False)),),
        frame_cache_dir=None,
        thumbnail_cache=cache,
    )

    tile = atlas.tile_for("photo-1")
    assert tile.kind == "photo"
    assert tile.jpeg_bytes == preview
