"""Unit tests for clip download logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


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
