"""Durable trip-discovery input cache contracts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_cache_reuses_unchanged_snapshot_and_filters_current_window(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache, source_fingerprint

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    fingerprint = source_fingerprint("https://immich.example", _buckets())
    source_range = _range(date(2025, 8, 11), date(2026, 8, 11))
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
    assert [asset.id for asset in loaded] == ["kept"]


def test_cache_invalidates_changed_buckets_server_age_and_missing_coverage(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache, source_fingerprint

    cache = TripInputCache(tmp_path)
    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    source_range = _range(date(2025, 8, 12), date(2026, 8, 12))
    fingerprint = source_fingerprint("https://immich.example", _buckets())
    cache.store(fingerprint, source_range, [_asset("one", now)], now=now)

    assert (
        cache.load(source_fingerprint("https://immich.example", _buckets(3)), source_range, now=now)
        is None
    )
    assert (
        cache.load(source_fingerprint("https://other.example", _buckets()), source_range, now=now)
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


def test_corrupt_cache_fails_open_without_leaking_server_url(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import TripInputCache, source_fingerprint

    cache = TripInputCache(tmp_path)
    cache.path.parent.mkdir(parents=True)
    cache.path.write_text("not json")
    fingerprint = source_fingerprint("https://private-immich.example", _buckets())

    assert cache.load(fingerprint, _range(date(2025, 8, 12), date(2026, 8, 12))) is None
    assert "private-immich" not in cache.path.read_text()


def test_load_or_fetch_avoids_second_full_year_query_when_unchanged(tmp_path: Path) -> None:
    from immich_memories.automation.trip_input_cache import load_or_fetch_trip_assets

    now = datetime(2026, 8, 12, 3, tzinfo=UTC)
    client = MagicMock()
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


def test_auto_runner_routes_trip_fetch_through_durable_cache() -> None:
    """The daily path must not bypass the cache helper."""
    source = Path("src/immich_memories/automation/runner.py").read_text()

    assert "load_or_fetch_trip_assets(" in source
    assert "AllAssetsService(client._async_client.search)" not in source
