"""Contracts for bounded, isolated video download prefetching."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@dataclass
class _Asset:
    id: str
    live_photo_video_id: str | None = None
    type: str = "VIDEO"
    original_file_name: str = "video.mp4"


class _FakeClient:
    def __init__(self, tracker: _DownloadTracker) -> None:
        self._tracker = tracker
        self.closed = False

    def download_asset(self, asset_id: str, output_path: Path) -> Path:
        self._tracker.download(asset_id, output_path)
        return output_path

    def close(self) -> None:
        self.closed = True


class _DownloadTracker:
    def __init__(self, root: Path, delay: float = 0.1) -> None:
        self.root = root
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.download_ids: list[str] = []
        self.clients: list[_FakeClient] = []
        self._lock = threading.Lock()

    def factory(self) -> _FakeClient:
        client = _FakeClient(self)
        self.clients.append(client)
        return client

    def download(self, asset_id: str, output_path: Path) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.download_ids.append(asset_id)
        try:
            if asset_id == "fail":
                raise OSError("network failure")
            time.sleep(self.delay)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(asset_id.encode())
        finally:
            with self._lock:
                self.active -= 1


class _Batch:
    def __init__(self, root: Path) -> None:
        self.root = root

    def download_or_get(self, client: _FakeClient, asset: _Asset) -> Path:
        resolved_id = asset.live_photo_video_id or asset.id
        path = self.root / f"{resolved_id}.mp4"
        return client.download_asset(resolved_id, path)


def test_prefetch_is_bounded_parallel_and_preserves_input_order(tmp_path: Path) -> None:
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path)
    assets = [_Asset(f"asset-{index}") for index in range(6)]
    coordinator = DownloadCoordinator(tracker.factory, _Batch(tmp_path), max_workers=3)

    results = coordinator.prefetch(assets)

    assert tracker.max_active == 3
    assert coordinator.max_observed_workers == 3
    assert list(results) == [asset.id for asset in assets]
    assert all(result.path is not None for result in results.values())
    assert all(client.closed for client in tracker.clients)


def test_prefetch_deduplicates_live_photo_ids_and_isolates_failures(tmp_path: Path) -> None:
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path, delay=0)
    assets = [
        _Asset("photo-1", live_photo_video_id="shared-video", type="IMAGE"),
        _Asset("photo-2", live_photo_video_id="shared-video", type="IMAGE"),
        _Asset("broken", live_photo_video_id="fail", type="IMAGE"),
        _Asset("static-photo", type="IMAGE"),
    ]
    results = DownloadCoordinator(tracker.factory, _Batch(tmp_path), max_workers=3).prefetch(assets)

    assert list(results) == [asset.id for asset in assets]
    assert tracker.download_ids.count("shared-video") == 1
    assert results["photo-1"].path == results["photo-2"].path
    assert results["broken"].error == "network failure"
    assert results["static-photo"].skipped
    assert all(client.closed for client in tracker.clients)


def test_one_worker_is_serial(tmp_path: Path) -> None:
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path)
    assets = [_Asset(f"asset-{index}") for index in range(3)]
    coordinator = DownloadCoordinator(tracker.factory, _Batch(tmp_path), max_workers=1)

    started = time.monotonic()
    coordinator.prefetch(assets)
    elapsed = time.monotonic() - started

    assert coordinator.max_observed_workers == 1
    assert elapsed >= 0.29


def test_api_error_is_isolated_and_later_download_in_same_worker_succeeds(tmp_path: Path) -> None:
    from immich_memories.api.immich import ImmichAPIError
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path, delay=0)

    class _ApiErrorBatch(_Batch):
        def download_or_get(self, client: _FakeClient, asset: _Asset) -> Path:
            if asset.id == "api-failure":
                raise ImmichAPIError("Bearer token-that-must-not-leak", status_code=503)
            return super().download_or_get(client, asset)

    results = DownloadCoordinator(
        tracker.factory, _ApiErrorBatch(tmp_path), max_workers=1
    ).prefetch([_Asset("api-failure"), _Asset("later-success")])

    assert results["api-failure"].error == "Bearer ***"
    assert results["later-success"].path is not None
    assert tracker.download_ids == ["later-success"]
    assert all(client.closed for client in tracker.clients)


def test_http_error_is_isolated_and_later_download_in_same_worker_succeeds(tmp_path: Path) -> None:
    import httpx

    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path, delay=0)

    class _HttpErrorBatch(_Batch):
        def download_or_get(self, client: _FakeClient, asset: _Asset) -> Path:
            if asset.id == "http-failure":
                request = httpx.Request("GET", "https://immich.example/download")
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("404 response", request=request, response=response)
            return super().download_or_get(client, asset)

    results = DownloadCoordinator(
        tracker.factory, _HttpErrorBatch(tmp_path), max_workers=1
    ).prefetch([_Asset("http-failure"), _Asset("later-success")])

    assert results["http-failure"].error == "404 response"
    assert results["later-success"].path is not None
    assert tracker.download_ids == ["later-success"]
    assert all(client.closed for client in tracker.clients)


def test_unexpected_download_error_still_closes_worker_client(tmp_path: Path) -> None:
    from immich_memories.processing.download_coordinator import DownloadCoordinator

    tracker = _DownloadTracker(tmp_path, delay=0)

    class _UnexpectedErrorBatch(_Batch):
        def download_or_get(self, client: _FakeClient, asset: _Asset) -> Path:
            raise TypeError("programming error")

    coordinator = DownloadCoordinator(
        tracker.factory, _UnexpectedErrorBatch(tmp_path), max_workers=1
    )

    with pytest.raises(TypeError, match="programming error"):
        coordinator.prefetch([_Asset("unexpected")])

    assert all(client.closed for client in tracker.clients)


def test_disabled_cache_prefetch_uses_run_owned_path_and_not_source_client(
    tmp_path: Path, monkeypatch
) -> None:
    from immich_memories.api.compatibility import ApiVersionPolicy
    from immich_memories.generate import _build_download_coordinator
    from immich_memories.processing import download_coordinator

    created_clients: list[object] = []

    class _WorkerClient:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False
            created_clients.append(self)

        def download_asset(self, asset_id: str, output_path: Path) -> Path:
            output_path.write_bytes(asset_id.encode())
            return output_path

        def close(self) -> None:
            self.closed = True

    source_client = MagicMock()
    source_client.base_url = "https://immich.example"
    source_client.api_key = "source-only-key"
    source_client.timeout = 45.0
    params = MagicMock()
    params.client = source_client
    params.config.immich.api_version = ApiVersionPolicy.AUTO
    params.config.analysis.download_workers = 1
    monkeypatch.setattr(download_coordinator, "SyncImmichClient", _WorkerClient)

    coordinator = _build_download_coordinator(params, None, tmp_path)
    assert coordinator is not None
    results = coordinator.prefetch([_Asset("run-owned")])

    assert results["run-owned"].path is not None
    assert results["run-owned"].path.is_relative_to(tmp_path / ".temporary_downloads")
    source_client.download_asset.assert_not_called()
    source_client.get_api_version.assert_not_called()
    assert all(client.closed for client in created_clients)


def test_build_coordinator_skips_opaque_caller_owned_client(tmp_path: Path) -> None:
    """Compatibility clients without connection metadata keep the serial path."""
    from immich_memories.generate import _build_download_coordinator

    params = MagicMock()
    params.client = object()

    assert _build_download_coordinator(params, None, tmp_path) is None


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("auto", "auto"),
        ("v2", "v2"),
        ("v3", "v3"),
    ],
)
def test_worker_factory_preserves_connection_and_api_policy_without_probing(
    monkeypatch, policy: str, expected: str
) -> None:
    from immich_memories.api.compatibility import ApiVersionPolicy
    from immich_memories.processing import download_coordinator

    captured: list[dict[str, object]] = []

    class _WorkerClient:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

    probes = 0

    def get_api_version(_self: object) -> object:
        nonlocal probes
        probes += 1
        raise AssertionError("factory construction must not contact Immich")

    source = type(
        "Source",
        (),
        {
            "base_url": "https://immich.example",
            "api_key": "test-key",
            "timeout": 45.0,
            "get_api_version": get_api_version,
        },
    )()
    monkeypatch.setattr(download_coordinator, "SyncImmichClient", _WorkerClient)

    download_coordinator.build_sync_client_factory(source, ApiVersionPolicy(policy))()

    assert captured == [
        {
            "base_url": "https://immich.example",
            "api_key": "test-key",
            "timeout": 45.0,
            "api_version": ApiVersionPolicy(expected),
        }
    ]
    assert probes == 0
