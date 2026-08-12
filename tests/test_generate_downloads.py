"""Unit tests for clip download logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestDownloadClip:
    def test_returns_local_path_when_exists(self, tmp_path: Path) -> None:
        """If clip.local_path exists on disk, skip downloading."""
        from immich_memories.generate_downloads import download_clip

        local = tmp_path / "existing.mp4"
        local.write_bytes(b"video")

        clip = MagicMock()  # WHY: VideoClipInfo is complex to construct
        clip.local_path = str(local)

        result = download_clip(client=None, video_cache=MagicMock(), clip=clip, output_dir=tmp_path)

        assert result == local

    def test_returns_none_when_no_client(self, tmp_path: Path) -> None:
        """If client is None and no local path, return None."""
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()  # WHY: VideoClipInfo is complex to construct
        clip.local_path = None

        result = download_clip(client=None, video_cache=MagicMock(), clip=clip, output_dir=tmp_path)

        assert result is None

    def test_delegates_to_burst_merge_when_burst_ids_present(self, tmp_path: Path) -> None:
        """If clip has burst IDs and trim points, delegates to burst merge."""
        from unittest.mock import patch

        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()  # WHY: VideoClipInfo is complex to construct
        clip.local_path = None
        clip.live_burst_video_ids = ["id1", "id2"]
        clip.live_burst_trim_points = [(0.0, 1.0), (0.0, 1.0)]

        mock_client = MagicMock()  # WHY: SyncImmichClient requires real server
        mock_cache = MagicMock()  # WHY: VideoDownloadCache needs disk setup

        with patch("immich_memories.generate_downloads._download_and_merge_burst") as mock_merge:
            mock_merge.return_value = tmp_path / "merged.mp4"
            result = download_clip(
                client=mock_client, video_cache=mock_cache, clip=clip, output_dir=tmp_path
            )

        mock_merge.assert_called_once()
        assert result == tmp_path / "merged.mp4"

    def test_falls_back_to_cache_download(self, tmp_path: Path) -> None:
        """If no local path and no burst, use video_cache.download_or_get."""
        from immich_memories.generate_downloads import download_clip

        clip = MagicMock()  # WHY: VideoClipInfo is complex to construct
        clip.local_path = None
        clip.live_burst_video_ids = None
        clip.live_burst_trim_points = None

        mock_client = MagicMock()  # WHY: SyncImmichClient requires real server
        mock_cache = MagicMock()  # WHY: VideoDownloadCache needs disk setup
        expected = tmp_path / "downloaded.mp4"
        mock_cache.download_or_get.return_value = expected

        result = download_clip(
            client=mock_client, video_cache=mock_cache, clip=clip, output_dir=tmp_path
        )

        assert result == expected
        mock_cache.download_or_get.assert_called_once_with(mock_client, clip.asset)


class TestAlignBurstSubset:
    def test_aligns_downloaded_to_trim_points(self, tmp_path: Path) -> None:
        """Downloaded clips should be matched back to their trim points by ID."""
        from immich_memories.generate_downloads import _align_burst_subset

        p1 = tmp_path / "id_a.mp4"
        p2 = tmp_path / "id_c.mp4"
        p1.touch()
        p2.touch()

        paths, trims = _align_burst_subset(
            downloaded_paths=[p1, p2],
            burst_ids=["id_a", "id_b", "id_c"],
            trim_points=[(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)],
        )

        assert len(paths) == 2
        assert paths[0].stem == "id_a"
        assert paths[1].stem == "id_c"
        assert trims == [(0.0, 1.0), (2.0, 3.0)]

    def test_returns_empty_when_no_matches(self, tmp_path: Path) -> None:
        """If no downloaded clips match burst IDs, return empty."""
        from immich_memories.generate_downloads import _align_burst_subset

        p1 = tmp_path / "unknown.mp4"
        p1.touch()

        paths, trims = _align_burst_subset(
            downloaded_paths=[p1],
            burst_ids=["id_a", "id_b"],
            trim_points=[(0.0, 1.0), (1.0, 2.0)],
        )

        assert paths == []
        assert trims == []


class TestBurstBatchManifest:
    @pytest.mark.parametrize("cached", [True, False], ids=["hit", "miss"])
    def test_component_path_updates_batch_without_finish_rescan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        cached: bool,
    ) -> None:
        import os
        import time

        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_burst_clips

        cache = VideoDownloadCache(tmp_path / "video-cache")
        component = cache.cache_dir / "bu" / "burst-id.MOV"
        old_mtime = time.time() - 3_600
        if cached:
            component.parent.mkdir(parents=True)
            component.write_bytes(b"cached-burst")
            os.utime(component, (old_mtime, old_mtime))

        client = MagicMock()

        def download(_asset_id: str, output_path: Path) -> Path:
            output_path.write_bytes(b"downloaded-burst")
            return output_path

        client.download_asset.side_effect = download
        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch:
            paths = _download_burst_clips(client, batch, ["burst-id"])

        assert paths == [component]
        assert len(scans) == 1
        assert component.stat().st_mtime > old_mtime
        assert client.download_asset.call_count == (0 if cached else 1)

    def test_failed_component_updates_batch_without_finish_rescan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_burst_clips

        cache = VideoDownloadCache(tmp_path / "video-cache")
        component = cache.cache_dir / "bu" / "burst-id.MOV"
        client = MagicMock()

        def partial_download(_asset_id: str, output_path: Path) -> None:
            output_path.write_bytes(b"partial")
            raise OSError("download failed")

        client.download_asset.side_effect = partial_download
        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch:
            assert _download_burst_clips(client, batch, ["burst-id"]) == []

        assert len(scans) == 1
        assert not component.exists()

    def test_propagated_component_error_updates_batch_without_finish_rescan(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_burst_clips

        cache = VideoDownloadCache(tmp_path / "video-cache")
        component = cache.cache_dir / "bu" / "burst-id.MOV"
        client = MagicMock()

        def partial_download(_asset_id: str, output_path: Path) -> None:
            output_path.write_bytes(b"partial")
            raise ValueError("download size limit exceeded")

        client.download_asset.side_effect = partial_download
        original_rglob = Path.rglob
        scans: list[Path] = []

        def counting_rglob(path: Path, pattern: str):
            if path == cache.cache_dir:
                scans.append(path)
            return original_rglob(path, pattern)

        monkeypatch.setattr(Path, "rglob", counting_rglob)
        with cache.begin_batch() as batch, pytest.raises(ValueError, match="size limit"):
            _download_burst_clips(client, batch, ["burst-id"])

        assert len(scans) == 1
        assert not component.exists()
