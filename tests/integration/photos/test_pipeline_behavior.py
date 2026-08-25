"""Behavioral tests for photos/photo_pipeline.py — rendering.

The batch render_photo_clips this file used to call was only ever reachable
from the two-budget chain. A photograph now renders one at a time, as the clip
it has already won a place as, through generate_clips._render_photo_as_clip.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.photo_pipeline import _render_single_photo
from tests.conftest import make_asset
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


class TestRenderSinglePhoto:
    def test_each_photo_renders_a_playable_clip(self, test_photo_landscape, tmp_path):
        """Every photo handed to the renderer becomes a valid video file."""
        assets = [make_asset(f"photo-{i}", original_file_name=f"IMG_{i}.jpg") for i in range(3)]
        config = PhotoConfig()
        config.duration = 2.0

        # WHY: mock download to copy our local test photo instead of hitting Immich.
        # The renderer calls download_fn(asset_id, raw_path) where raw_path is the
        # full destination file path (not a directory).
        def download_fn(asset_id: str, dest_path: Path) -> None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(test_photo_landscape, dest_path)

        clips = [
            _render_single_photo(
                asset=asset,
                config=config,
                target_w=1280,
                target_h=720,
                work_dir=tmp_path / "photos",
                download_fn=download_fn,
            )
            for asset in assets
        ]

        rendered = [clip for clip in clips if clip is not None]
        assert rendered
        for clip in rendered:
            assert clip.path.exists()
            probe = ffprobe_json(clip.path)
            assert has_stream(probe, "video")
            assert get_duration(probe) > 1.0

    def test_an_undownloadable_photo_yields_no_clip(self, tmp_path):
        """A photo that cannot be fetched costs its own clip, not the run."""

        def download_fn(asset_id: str, dest_path: Path) -> None:
            raise OSError("no such asset")

        clip = _render_single_photo(
            asset=make_asset("missing", original_file_name="IMG_0.jpg"),
            config=PhotoConfig(),
            target_w=640,
            target_h=360,
            work_dir=tmp_path / "photos",
            download_fn=download_fn,
        )

        assert clip is None
