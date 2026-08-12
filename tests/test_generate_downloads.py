"""Unit tests for clip download logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.api.sync_client import SyncImmichClient
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.processing.download_coordinator import DownloadResult
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

    def test_burst_merge_reuses_prefetched_components(self, tmp_path: Path) -> None:
        """Serial live-photo merge must not download components prefetch already fetched."""
        from unittest.mock import patch

        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_and_merge_burst

        cache = VideoDownloadCache(tmp_path / "video-cache")
        first = cache.cache_dir / "id" / "id-one.MOV"
        second = cache.cache_dir / "id" / "id-two.MOV"
        first.parent.mkdir(parents=True)
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        clip = MagicMock()
        clip.asset.id = "burst-parent"
        clip.live_burst_video_ids = ["id-one", "id-two"]
        clip.live_burst_trim_points = [(0.0, 1.0), (1.0, 2.0)]
        clip.live_burst_shutter_timestamps = None
        client = MagicMock()
        merged = tmp_path / "merged.mp4"

        with (
            cache.begin_batch() as batch,
            patch("immich_memories.generate_downloads._try_merge_burst", return_value=merged),
        ):
            result = _download_and_merge_burst(
                client,
                batch,
                clip,
                tmp_path,
                prefetched_paths={"id-one": first, "id-two": second},
            )

        assert result == merged
        client.download_asset.assert_not_called()

    def test_burst_with_all_failed_prefetches_never_retries_components(
        self, tmp_path: Path
    ) -> None:
        """A prefetched failure is final for the current burst generation run."""
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_and_merge_burst

        cache = VideoDownloadCache(tmp_path / "video-cache")
        clip = MagicMock()
        clip.asset.id = "burst-parent"
        clip.live_burst_video_ids = ["id-one", "id-two"]
        clip.live_burst_trim_points = [(0.0, 1.0), (1.0, 2.0)]
        clip.live_burst_shutter_timestamps = None
        client = MagicMock()

        with cache.begin_batch() as batch:
            result = _download_and_merge_burst(
                client,
                batch,
                clip,
                tmp_path,
                prefetched_paths={"id-one": None, "id-two": None},
            )

        assert result is None
        client.download_asset.assert_not_called()

    def test_failed_prefetched_burst_merge_never_retries_components(self, tmp_path: Path) -> None:
        """A merge failure does not fall back to a serial component download after prefetch."""
        from unittest.mock import patch

        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_and_merge_burst

        cache = VideoDownloadCache(tmp_path / "video-cache")
        component = cache.cache_dir / "id" / "id-one.MOV"
        component.parent.mkdir(parents=True)
        component.write_bytes(b"one")
        clip = MagicMock()
        clip.asset.id = "burst-parent"
        clip.live_burst_video_ids = ["id-one", "id-two"]
        clip.live_burst_trim_points = [(0.0, 1.0), (1.0, 2.0)]
        clip.live_burst_shutter_timestamps = None
        client = MagicMock()

        with (
            cache.begin_batch() as batch,
            patch("immich_memories.generate_downloads._try_merge_burst", return_value=None),
        ):
            result = _download_and_merge_burst(
                client,
                batch,
                clip,
                tmp_path,
                prefetched_paths={"id-one": component, "id-two": None},
            )

        assert result is None
        client.download_asset.assert_not_called()

    def test_burst_failure_log_does_not_include_raw_exception(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Burst download diagnostics do not include raw server error text or tracebacks."""
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.generate_downloads import _download_burst_clips

        cache = VideoDownloadCache(tmp_path / "video-cache")
        client = MagicMock()
        client.download_asset.side_effect = OSError("token=unlabelled-secret-value")

        with cache.begin_batch() as batch:
            assert _download_burst_clips(client, batch, ["burst-id"]) == []

        assert "unlabelled-secret-value" not in caplog.text


def test_extract_clips_uses_prefetched_video_before_serial_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Video network work is prefetched, while segment extraction remains serial."""
    from immich_memories import generate_clips
    from immich_memories.processing.probe_cache import ProbeCache

    clip = make_clip("prefetched-video", duration=2.0)
    downloaded = tmp_path / "source.mp4"
    downloaded.write_bytes(b"video")
    segment = tmp_path / "segment.mp4"
    segment.write_bytes(b"segment")
    params = GenerationParams(
        clips=[clip],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=MagicMock(spec=SyncImmichClient),
    )
    calls: list[object] = []
    probe_cache = ProbeCache()
    observed_probe_caches: list[ProbeCache | None] = []

    class _Coordinator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append((args, kwargs))

        def prefetch(
            self, assets: list[object], progress: object = None
        ) -> dict[str, DownloadResult]:
            calls.append(assets)
            return {clip.asset.id: DownloadResult(downloaded)}

    monkeypatch.setattr(generate_clips, "DownloadCoordinator", _Coordinator)
    monkeypatch.setattr(
        "immich_memories.generate_downloads.download_clip",
        lambda *_args: (_ for _ in ()).throw(AssertionError("prefetch result was ignored")),
    )

    def _probe_duration(_path: Path, *, probe_cache: ProbeCache | None = None) -> float:
        observed_probe_caches.append(probe_cache)
        return 2.0

    monkeypatch.setattr(generate_clips, "_probe_file_duration", _probe_duration)
    monkeypatch.setattr(
        "immich_memories.processing.clips.extract_clip", lambda *_args, **_kwargs: segment
    )

    assembly_clips = generate_clips._extract_clips(
        params,
        MagicMock(),
        tmp_path,
        probe_cache=probe_cache,
    )

    assert len(calls) == 2
    assert calls[1] == [clip.asset]
    assert observed_probe_caches == [probe_cache]
    assert [assembly_clip.path for assembly_clip in assembly_clips] == [segment]


def test_download_factory_uses_config_connection_policy_and_source_timeout_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker clients are isolated clones with configuration-owned connection policy."""
    from immich_memories import generate_clips
    from immich_memories.api.compatibility import ApiVersionPolicy

    constructed: list[dict[str, object]] = []
    closed: list[str] = []

    class _AsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            if args:
                kwargs = {
                    "base_url": args[0],
                    "api_key": args[1],
                    **kwargs,
                }
            constructed.append(kwargs)
            self.base_url = str(kwargs["base_url"])
            self.api_key = str(kwargs["api_key"])
            self.timeout = float(kwargs["timeout"])

        async def close(self) -> None:
            closed.append(self.base_url)

    monkeypatch.setattr("immich_memories.api.immich.ImmichClient", _AsyncClient)
    source = SyncImmichClient("https://source.invalid", "source-key", timeout=47.0)
    source.get_api_version = MagicMock(side_effect=AssertionError("must not probe"))
    params = GenerationParams(
        clips=[],
        output_path=Path("memory.mp4"),
        config=Config(
            immich={
                "url": "https://configured.invalid",
                "api_key": "configured-key",
                "api_version": "v3",
            }
        ),
        client=source,
    )

    try:
        factory = generate_clips._download_client_factory(params)
        assert factory is not None
        worker = factory()

        assert isinstance(worker, SyncImmichClient)
        assert constructed[-1] == {
            "base_url": "https://configured.invalid",
            "api_key": "configured-key",
            "api_version": ApiVersionPolicy.V3,
            "timeout": 47.0,
        }
        source.get_api_version.assert_not_called()
    finally:
        worker.close()
        source.close()

    assert closed == ["https://configured.invalid", "https://source.invalid"]


def test_real_sync_worker_owns_and_closes_its_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An isolated real sync worker creates and closes its loop in the worker thread."""
    import threading

    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    main_thread = threading.get_ident()
    worker_threads: list[int] = []

    class _AsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def download_asset(self, _asset_id: str, output_path: Path) -> Path:
            worker_threads.append(threading.get_ident())
            output_path.write_bytes(b"video")
            return output_path

        async def close(self) -> None:
            worker_threads.append(threading.get_ident())

    monkeypatch.setattr("immich_memories.api.immich.ImmichClient", _AsyncClient)
    created: list[SyncImmichClient] = []

    def factory() -> SyncImmichClient:
        worker = SyncImmichClient("https://configured.invalid", "configured-key")
        created.append(worker)
        return worker

    cache = VideoDownloadCache(tmp_path / "video-cache")
    with cache.begin_batch() as batch:
        result = DownloadCoordinator(factory, batch, max_workers=1).prefetch(
            [make_clip("worker").asset]
        )

    source = SyncImmichClient("https://source.invalid", "source-key")
    source.close()

    assert result["worker"].path is not None
    assert worker_threads[:2] == [worker_threads[0], worker_threads[0]]
    assert worker_threads[0] != main_thread
    assert worker_threads[-1] == main_thread
    assert len(created) == 1
    assert created[0]._loop is not None and created[0]._loop.is_closed()


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
