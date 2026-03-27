"""Behavioral tests for photos/photo_pipeline.py — scoring and rendering."""

from __future__ import annotations

import shutil
from pathlib import Path

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.photo_pipeline import render_photo_clips
from tests.conftest import make_asset
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg


@requires_ffmpeg
class TestRenderPhotoClips:
    def test_renders_correct_number_of_clips(self, test_photo_landscape, tmp_path):
        """N photo assets should produce <=N AssemblyClips with valid video files."""
        assets = [make_asset(f"photo-{i}", original_file_name=f"IMG_{i}.jpg") for i in range(3)]
        config = PhotoConfig()
        config.duration = 2.0

        # WHY: mock download to copy our local test photo instead of hitting Immich.
        # The pipeline calls download_fn(asset_id, raw_path) where raw_path is the
        # full destination file path (not a directory).
        def download_fn(asset_id: str, dest_path: Path) -> None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(test_photo_landscape, dest_path)

        clips = render_photo_clips(
            assets=assets,
            config=config,
            target_w=1280,
            target_h=720,
            work_dir=tmp_path / "photos",
            download_fn=download_fn,
            video_clip_count=10,
        )

        assert len(clips) <= 3
        assert len(clips) > 0
        for clip in clips:
            assert clip.path.exists()
            probe = ffprobe_json(clip.path)
            assert has_stream(probe, "video")
            assert get_duration(probe) > 1.0

    def test_empty_assets_returns_empty(self, tmp_path):
        """No assets should produce no clips without error."""
        config = PhotoConfig()

        clips = render_photo_clips(
            assets=[],
            config=config,
            target_w=1280,
            target_h=720,
            work_dir=tmp_path / "photos",
            download_fn=lambda aid, p: p / f"{aid}.jpg",
            video_clip_count=10,
        )

        assert clips == []
