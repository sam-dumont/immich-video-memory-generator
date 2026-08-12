"""Unit tests for clip download logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tests.conftest import make_clip


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
        assert local.exists()

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


def test_disabled_cache_extraction_preserves_existing_local_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Only run-owned temporary downloads are cleaned up after extraction."""
    from immich_memories.generate_clips import _extract_clips

    local = tmp_path / "caller-owned.mp4"
    local.write_bytes(b"caller video")
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"segment")
    clip = make_clip("existing-local", duration=5.0)
    clip.local_path = str(local)
    params = MagicMock()
    params.clips = [clip]
    params.progress_callback = None
    params.clip_segments = {}
    params.clip_rotations = {}
    params.config = MagicMock()

    monkeypatch.setattr("immich_memories.generate_downloads.download_clip", lambda *_args: local)
    monkeypatch.setattr(
        "immich_memories.processing.clips.extract_clip", lambda *_args, **_kwargs: segment
    )
    monkeypatch.setattr("immich_memories.generate_clips._probe_file_duration", lambda _path: 5.0)

    _extract_clips(params, None, tmp_path)

    assert local.exists()


def test_disabled_cache_download_is_run_owned_and_cleaned(tmp_path: Path) -> None:
    """Disabled cache downloads stay under the run directory, never OS temp or persistent cache."""
    from immich_memories.generate_clips import _cleanup_temp_dirs
    from immich_memories.generate_downloads import download_clip

    clip = MagicMock()
    clip.local_path = None
    clip.live_burst_video_ids = None
    clip.live_burst_trim_points = None
    clip.asset.id = "ab-temporary"
    clip.asset.live_photo_video_id = None
    clip.asset.original_file_name = "clip.MOV"
    client = MagicMock()
    client.download_asset.side_effect = lambda _asset_id, path: path.write_bytes(b"video")

    path = download_clip(client, None, clip, tmp_path)

    assert path is not None
    assert path.is_relative_to(tmp_path / ".temporary_downloads")
    _cleanup_temp_dirs(tmp_path)
    assert not path.exists()


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
