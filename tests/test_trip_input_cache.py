"""Durable trip-discovery input cache contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from immich_memories.api.models import Asset, AssetType, ExifInfo, TimeBucket
from immich_memories.timeperiod import DateRange


def _asset(asset_id: str, created: datetime, *, latitude: float = 48.85) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.IMAGE,
        fileCreatedAt=created,
        fileModifiedAt=created,
        updatedAt=created,
        exifInfo=ExifInfo(latitude=latitude, longitude=2.35, city="Paris", country="France"),
    )


def _range(start: date, end: date) -> DateRange:
    return DateRange(
        start=datetime.combine(start, datetime.min.time()),
        end=datetime.combine(end, datetime.max.time()),
    )


def _buckets(count: int = 2) -> list[TimeBucket]:
    return [TimeBucket(count=count, timeBucket="2026-01-01T00:00:00.000Z")]


def _fingerprint(server: str = "https://immich.example", api_key: str = "api-key-one") -> str:
    from immich_memories.automation.trip_input_cache import source_fingerprint

    return source_fingerprint(server, api_key, _buckets())


def test_cache_reuses_unchanged_snapshot_and_filters_current_window(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    fingerprint = _fingerprint()
    source_range = _range(date(2025, 8, 11), date(2026, 8, 19))
    cache.store(
        fingerprint,
        source_range,
        [
            _asset("expired", datetime(2025, 8, 11, 12, tzinfo=UTC)),
            _asset("kept", datetime(2026, 1, 5, 12, tzinfo=UTC)),
        ],
        now=now,
    )

    loaded = cache.load(
        fingerprint,
        _range(date(2025, 8, 12), date(2026, 8, 12)),
        now=now + timedelta(days=1),
    )

    assert loaded is not None
    assert [asset.id for asset in loaded.assets] == ["kept"]
    serialized = cache.path.read_text()
    assert "immich.example" not in serialized
    assert "api-key-one" not in serialized


def test_cache_invalidates_changed_buckets_server_age_and_missing_coverage(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache, source_fingerprint

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    source_range = _range(date(2025, 8, 12), date(2026, 8, 12))
    fingerprint = _fingerprint()
    cache.store(fingerprint, source_range, [_asset("one", now)], now=now)

    assert (
        cache.load(
            source_fingerprint("https://immich.example", "api-key-one", _buckets(3)),
            source_range,
            now=now,
        )
        is None
    )
    assert (
        cache.load(
            source_fingerprint("https://other.example", "api-key-one", _buckets()),
            source_range,
            now=now,
        )
        is None
    )
    assert (
        cache.load(
            source_fingerprint("https://immich.example", "api-key-two", _buckets()),
            source_range,
            now=now,
        )
        is None
    )
    assert cache.load(fingerprint, source_range, now=now + timedelta(days=8)) is None
    assert (
        cache.load(
            fingerprint,
            _range(date(2025, 8, 11), date(2026, 8, 12)),
            now=now,
        )
        is None
    )
    assert (
        cache.load(
            fingerprint,
            _range(date(2025, 8, 12), date(2026, 8, 13)),
            now=now,
        )
        is None
    )


def test_corrupt_cache_fails_open_without_leaking_server_url(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache, source_fingerprint

    cache = TripInputCache(tmp_path)
    cache.path.parent.mkdir(parents=True)
    cache.path.write_text("not json")
    fingerprint = source_fingerprint(
        "https://private-immich.example", "private-api-key", _buckets()
    )

    assert cache.load(fingerprint, _range(date(2025, 8, 12), date(2026, 8, 12))) is None
    assert "private-immich" not in cache.path.read_text()
    assert "private-api-key" not in cache.path.read_text()


def test_load_or_fetch_avoids_second_full_year_query_when_unchanged(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock()
    client.api_key = "api-key-one"
    client.search_metadata.return_value.total = 0
    query = object()
    service = MagicMock()
    service.get_assets_for_date_range.return_value = query
    client._run.return_value = [_asset("trip-photo", datetime(2026, 1, 5, tzinfo=UTC))]

    with patch("immich_memories.api.all_assets_service.AllAssetsService", return_value=service):
        first = load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 12), date(2026, 8, 12)),
            now=now,
        )
        second = load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 13), date(2026, 8, 13)),
            now=now + timedelta(days=1),
        )

    assert [asset.id for asset in first] == ["trip-photo"]
    assert [asset.id for asset in second] == ["trip-photo"]
    service.get_assets_for_date_range.assert_called_once()
    client._run.assert_called_once_with(query)


def test_changed_bucket_count_refetches_and_replaces_snapshot(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock()
    client.api_key = "api-key-one"
    query = object()
    service = MagicMock()
    service.get_assets_for_date_range.return_value = query
    client._run.side_effect = [
        [_asset("first", datetime(2026, 1, 5, tzinfo=UTC))],
        [_asset("second", datetime(2026, 1, 6, tzinfo=UTC))],
    ]

    with patch("immich_memories.api.all_assets_service.AllAssetsService", return_value=service):
        load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(1),
            requested_range=_range(date(2025, 8, 12), date(2026, 8, 12)),
            now=now,
        )
        refreshed = load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(2),
            requested_range=_range(date(2025, 8, 13), date(2026, 8, 13)),
            now=now + timedelta(days=1),
        )

    assert [asset.id for asset in refreshed] == ["second"]
    assert client._run.call_count == 2
    payload = json.loads((tmp_path / "trip-inputs" / "assets.json").read_text())
    assert payload["asset_count"] == 1


def test_same_count_metadata_update_forces_full_refetch(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock()
    client.api_key = "api-key-one"
    client.search_metadata.return_value.total = 1
    client._run.side_effect = [
        [_asset("before-edit", datetime(2026, 1, 5, tzinfo=UTC))],
        [_asset("after-edit", datetime(2026, 1, 5, tzinfo=UTC), latitude=40.71)],
    ]

    with patch("immich_memories.api.all_assets_service.AllAssetsService") as service_type:
        service_type.return_value.get_assets_for_date_range.side_effect = [object(), object()]
        load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 12), date(2026, 8, 12)),
            now=now,
        )
        refreshed = load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 13), date(2026, 8, 13)),
            now=now + timedelta(days=1),
        )

    assert [asset.id for asset in refreshed] == ["after-edit"]
    client.search_metadata.assert_called_once_with(updated_after=now, page=1, size=1)
    assert client._run.call_count == 2


def test_fetch_covers_daily_window_through_cache_horizon(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock()
    client.api_key = "api-key-one"
    client._run.return_value = []

    with patch("immich_memories.api.all_assets_service.AllAssetsService") as service_type:
        load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 12), date(2026, 8, 12)),
            now=now,
        )

    fetched_range = service_type.return_value.get_assets_for_date_range.call_args.args[0]
    assert fetched_range.start.date() == date(2025, 8, 12)
    assert fetched_range.end.date() == date(2026, 8, 19)


def test_search_metadata_serializes_updated_after() -> None:
    import asyncio

    from immich_memories.api.search_service import SearchService

    request = AsyncMock()
    request.return_value = {"assets": {"items": [], "total": 0}}
    updated_after = datetime(2026, 8, 12, 3, tzinfo=UTC)

    asyncio.run(SearchService(request).search_metadata(updated_after=updated_after, size=1))

    assert request.call_args.kwargs["json"]["updatedAfter"] == updated_after.isoformat()


def test_failed_atomic_replace_keeps_previous_snapshot(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    source_range = _range(date(2025, 8, 12), date(2026, 8, 19))
    cache.store(_fingerprint(), source_range, [_asset("kept", now)], now=now)

    with patch.object(Path, "replace", side_effect=OSError("read-only filesystem")):
        cache.store(
            _fingerprint(),
            source_range,
            [_asset("not-written", now)],
            now=now + timedelta(hours=1),
        )

    loaded = cache.load(_fingerprint(), source_range, now=now + timedelta(hours=1))
    assert loaded is not None
    assert [asset.id for asset in loaded.assets] == ["kept"]
    assert list(cache.path.parent.glob(".assets-*.tmp")) == []


def test_freshness_probe_failure_falls_back_to_full_fetch(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock(api_key="api-key-one")
    client.search_metadata.side_effect = OSError("connection reset")
    client._run.side_effect = [
        [_asset("cached", datetime(2026, 1, 5, tzinfo=UTC))],
        [_asset("refetched", datetime(2026, 1, 6, tzinfo=UTC))],
    ]

    with patch("immich_memories.api.all_assets_service.AllAssetsService") as service_type:
        service_type.return_value.get_assets_for_date_range.side_effect = [object(), object()]
        load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 12), date(2026, 8, 12)),
            now=now,
        )
        refreshed = load_or_fetch_trip_assets(
            client,
            cache_root=tmp_path,
            server_url="https://immich.example",
            buckets=_buckets(),
            requested_range=_range(date(2025, 8, 13), date(2026, 8, 13)),
            now=now + timedelta(days=1),
        )

    assert [asset.id for asset in refreshed] == ["refetched"]
    assert client._run.call_count == 2


def test_concurrent_writers_leave_one_complete_valid_snapshot(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    source_range = _range(date(2025, 8, 12), date(2026, 8, 19))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                cache.store,
                _fingerprint(),
                source_range,
                [_asset(asset_id, now)],
                now=now,
            )
            for asset_id in ("writer-one", "writer-two")
        ]
        for future in futures:
            future.result()

    loaded = cache.load(_fingerprint(), source_range, now=now)
    assert loaded is not None
    assert [asset.id for asset in loaded.assets] in (["writer-one"], ["writer-two"])
    assert list(cache.path.parent.glob(".assets-*.tmp")) == []
