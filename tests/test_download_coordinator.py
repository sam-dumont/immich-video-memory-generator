"""Behaviour tests for bounded generation download prefetching."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from immich_memories.api.models import AssetType
from immich_memories.config_models import AnalysisConfig
from tests.conftest import make_asset


class _TrackingCacheBatch:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path
        self._lock = threading.Lock()
        self.active_workers = 0
        self.max_observed_workers = 0
        self.downloaded_asset_ids: list[str] = []

    def download_or_get(self, client: object, asset: object) -> Path:
        with self._lock:
            self.active_workers += 1
            self.max_observed_workers = max(self.max_observed_workers, self.active_workers)
        try:
            with self._lock:
                self.downloaded_asset_ids.append(asset.id)
            time.sleep(0.1)
            path = self._tmp_path / f"{asset.id}.mp4"
            path.write_bytes(b"video")
            return path
        finally:
            with self._lock:
                self.active_workers -= 1


class _Client:
    def __init__(self, closed: list[bool]) -> None:
        self._closed = closed

    def close(self) -> None:
        self._closed.append(True)


class _ThreadTrackingClient(_Client):
    def __init__(
        self, closed: list[bool], created_threads: list[int], closed_threads: list[int]
    ) -> None:
        super().__init__(closed)
        self._created_threads = created_threads
        self._closed_threads = closed_threads
        self._created_threads.append(threading.get_ident())

    def close(self) -> None:
        self._closed_threads.append(threading.get_ident())
        super().close()


class _CloseFailingClient(_Client):
    def close(self) -> None:
        raise ValueError("key=must-not-leak")


class _FailingCacheBatch(_TrackingCacheBatch):
    def download_or_get(self, client: object, asset: object) -> Path:
        if asset.id == "broken":
            raise OSError("secret server detail")
        return super().download_or_get(client, asset)


class _ApiFailingCacheBatch(_TrackingCacheBatch):
    def download_or_get(self, client: object, asset: object) -> Path:
        from immich_memories.api.immich import ImmichAPIError

        if asset.id == "broken":
            raise ImmichAPIError("Bearer a-secret-token", status_code=503)
        return super().download_or_get(client, asset)


class _ValueFailingCacheBatch(_TrackingCacheBatch):
    def download_or_get(self, client: object, asset: object) -> Path:
        if asset.id == "broken":
            raise ValueError("download size limit exceeded")
        return super().download_or_get(client, asset)


class _InterruptingCacheBatch(_TrackingCacheBatch):
    def download_or_get(self, client: object, asset: object) -> Path:
        raise KeyboardInterrupt("cancel generation")


class _OutOfOrderCacheBatch(_TrackingCacheBatch):
    def download_or_get(self, client: object, asset: object) -> Path:
        time.sleep(0.1 if asset.id == "first" else 0.01)
        return super().download_or_get(client, asset)


def test_download_workers_default_and_bounds() -> None:
    """Generation uses three bounded download workers unless configured otherwise."""
    assert AnalysisConfig().download_workers == 3
    assert AnalysisConfig(download_workers=8).download_workers == 8
    with pytest.raises(ValueError):
        AnalysisConfig(download_workers=0)
    with pytest.raises(ValueError):
        AnalysisConfig(download_workers=9)


def test_coordinator_rejects_non_positive_worker_bound(tmp_path: Path) -> None:
    """The public coordinator cannot be created with an invalid worker bound."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    with pytest.raises(ValueError, match="max_workers"):
        DownloadCoordinator(lambda: _Client([]), _TrackingCacheBatch(tmp_path), max_workers=0)


def test_prefetch_is_bounded_fast_and_ordered(tmp_path: Path) -> None:
    """Prefetch runs at the configured bound while retaining source order."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    assets = [make_asset(f"asset-{number}") for number in range(6)]
    cache_batch = _TrackingCacheBatch(tmp_path)
    closed: list[bool] = []
    coordinator = DownloadCoordinator(
        client_factory=lambda: _Client(closed), cache_batch=cache_batch, max_workers=3
    )

    started = time.monotonic()
    results = coordinator.prefetch(assets)
    elapsed = time.monotonic() - started

    assert cache_batch.max_observed_workers == 3
    assert elapsed < 0.35
    assert list(results) == [asset.id for asset in assets]
    assert all(result.path is not None for result in results.values())
    assert len(closed) == 3


def test_workers_construct_and_close_clients_in_the_worker_thread(tmp_path: Path) -> None:
    """Each independent client stays wholly inside the worker that owns it."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    main_thread = threading.get_ident()
    closed: list[bool] = []
    created_threads: list[int] = []
    closed_threads: list[int] = []
    DownloadCoordinator(
        client_factory=lambda: _ThreadTrackingClient(closed, created_threads, closed_threads),
        cache_batch=_TrackingCacheBatch(tmp_path),
        max_workers=2,
    ).prefetch([make_asset("one"), make_asset("two")])

    assert created_threads
    assert set(created_threads) == set(closed_threads)
    assert len(created_threads) == len(closed_threads)
    assert all(thread_id != main_thread for thread_id in created_threads)


def test_close_failure_does_not_expose_detail_or_cancel_results(tmp_path: Path) -> None:
    """Cleanup errors are contained after workers finish their downloads."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    results = DownloadCoordinator(
        client_factory=lambda: _CloseFailingClient([]),
        cache_batch=_TrackingCacheBatch(tmp_path),
        max_workers=1,
    ).prefetch([make_asset("one"), make_asset("two")])

    assert all(result.path is not None for result in results.values())


def test_base_exception_propagates_after_worker_client_closes_in_its_thread(tmp_path: Path) -> None:
    """Cancellation-equivalent exceptions preserve their type while worker cleanup still runs."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    main_thread = threading.get_ident()
    closed: list[bool] = []
    created_threads: list[int] = []
    closed_threads: list[int] = []
    coordinator = DownloadCoordinator(
        client_factory=lambda: _ThreadTrackingClient(closed, created_threads, closed_threads),
        cache_batch=_InterruptingCacheBatch(tmp_path),
        max_workers=1,
    )

    with pytest.raises(KeyboardInterrupt, match="cancel generation"):
        coordinator.prefetch([make_asset("cancelled")])

    assert created_threads == closed_threads
    assert created_threads == [created_threads[0]]
    assert created_threads[0] != main_thread


def test_factory_failure_does_not_cancel_an_independent_worker(tmp_path: Path) -> None:
    """One worker failing to initialize leaves another independent worker usable."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    attempts = 0

    def factory() -> _Client:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("api-key=must-not-leak")
        return _Client([])

    results = DownloadCoordinator(
        client_factory=factory, cache_batch=_TrackingCacheBatch(tmp_path), max_workers=2
    ).prefetch([make_asset("one"), make_asset("two")])

    assert any(result.path is not None for result in results.values())
    assert all(result.error in (None, "download failed") for result in results.values())


def test_prefetch_with_one_worker_is_serial(tmp_path: Path) -> None:
    """A one-worker coordinator retains the serial download behavior."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    assets = [make_asset(f"asset-{number}") for number in range(3)]
    coordinator = DownloadCoordinator(
        client_factory=lambda: _Client([]), cache_batch=_TrackingCacheBatch(tmp_path), max_workers=1
    )

    started = time.monotonic()
    coordinator.prefetch(assets)
    elapsed = time.monotonic() - started

    assert coordinator.max_observed_workers == 1
    assert elapsed >= 0.29


def test_prefetch_reports_progress_in_source_order(tmp_path: Path) -> None:
    """Progress updates retain source ordering even when downloads finish out of order."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    assets = [make_asset("first"), make_asset("second")]
    progress: list[tuple[int, int, object]] = []
    DownloadCoordinator(
        client_factory=lambda: _Client([]),
        cache_batch=_OutOfOrderCacheBatch(tmp_path),
        max_workers=2,
    ).prefetch(assets, progress=lambda done, total, result: progress.append((done, total, result)))

    assert [(done, total) for done, total, _result in progress] == [(1, 2), (2, 2)]
    assert [result.path.name for _done, _total, result in progress] == ["first.mp4", "second.mp4"]


def test_prefetch_reports_progress_for_each_input_including_duplicates(tmp_path: Path) -> None:
    """Progress preserves every input position while result keys retain legacy uniqueness."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    first = make_asset("duplicate")
    second = make_asset("duplicate")
    updates: list[tuple[int, int]] = []
    results = DownloadCoordinator(
        client_factory=lambda: _Client([]), cache_batch=_TrackingCacheBatch(tmp_path), max_workers=1
    ).prefetch([first, second], progress=lambda done, total, _: updates.append((done, total)))

    assert list(results) == ["duplicate"]
    assert updates == [(1, 2), (2, 2)]


def test_prefetch_downloads_a_shared_live_photo_component_once(tmp_path: Path) -> None:
    """Different live-photo assets sharing one video component reuse one download."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    first = make_asset("first-live-photo").model_copy(
        update={"live_photo_video_id": "shared-video"}
    )
    second = make_asset("second-live-photo").model_copy(
        update={"live_photo_video_id": "shared-video"}
    )
    cache_batch = _TrackingCacheBatch(tmp_path)
    coordinator = DownloadCoordinator(
        client_factory=lambda: _Client([]), cache_batch=cache_batch, max_workers=2
    )

    results = coordinator.prefetch([first, second])

    assert cache_batch.downloaded_asset_ids == [first.id]
    assert list(results) == [first.id, second.id]
    assert results[first.id].path == results[second.id].path


def test_prefetch_reports_one_failure_without_cancelling_other_downloads(tmp_path: Path) -> None:
    """A failed download is isolated and returns no raw server detail."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    good = make_asset("good")
    broken = make_asset("broken")
    coordinator = DownloadCoordinator(
        client_factory=lambda: _Client([]), cache_batch=_FailingCacheBatch(tmp_path), max_workers=2
    )

    results = coordinator.prefetch([good, broken])

    assert results[good.id].path is not None
    assert results[broken.id].path is None
    assert results[broken.id].error == "download failed"


def test_prefetch_isolates_api_failures_without_returning_secret_details(tmp_path: Path) -> None:
    """API download failures do not cancel later work or expose credentials."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    good = make_asset("good")
    broken = make_asset("broken")
    coordinator = DownloadCoordinator(
        client_factory=lambda: _Client([]),
        cache_batch=_ApiFailingCacheBatch(tmp_path),
        max_workers=1,
    )

    results = coordinator.prefetch([broken, good])

    assert results[broken.id].error == "download failed"
    assert "secret" not in results[broken.id].error
    assert results[good.id].path is not None


def test_prefetch_isolates_download_validation_failures(tmp_path: Path) -> None:
    """A per-asset validation error does not prevent later downloads in that worker."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    good = make_asset("good")
    broken = make_asset("broken")
    results = DownloadCoordinator(
        client_factory=lambda: _Client([]),
        cache_batch=_ValueFailingCacheBatch(tmp_path),
        max_workers=1,
    ).prefetch([broken, good])

    assert results[broken.id].error == "download failed"
    assert results[good.id].path is not None


def test_prefetch_skips_static_photos(tmp_path: Path) -> None:
    """Static photos stay on the sequential photo-rendering path."""
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    photo = make_asset("static-photo").model_copy(update={"type": AssetType.IMAGE})
    cache_batch = _TrackingCacheBatch(tmp_path)
    results = DownloadCoordinator(
        client_factory=lambda: _Client([]), cache_batch=cache_batch, max_workers=1
    ).prefetch([photo])

    assert cache_batch.downloaded_asset_ids == []
    assert results[photo.id].skipped is True
