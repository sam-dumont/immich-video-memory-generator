"""Durable, identity-checked inputs for automatic trip discovery."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.api.models import Asset, TimeBucket
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 2
_MAX_AGE = timedelta(days=7)


def source_fingerprint(
    server_url: str,
    api_key: str,
    buckets: list[TimeBucket],
) -> str:
    """Identify the credential scope and library snapshot without storing secrets."""
    server_digest = hashlib.sha256(server_url.rstrip("/").encode()).hexdigest()
    credential_digest = hashlib.sha256(api_key.encode()).hexdigest()
    identity = {
        "schema_version": _SCHEMA_VERSION,
        "server": server_digest,
        "credential": credential_digest,
        "buckets": sorted((bucket.time_bucket, bucket.count) for bucket in buckets),
    }
    encoded = json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class TripInputSnapshot:
    """Validated cache data plus the watermark used for a cheap freshness query."""

    fetched_at: datetime
    assets: list[Asset]


class TripInputCache:
    """Atomic JSON cache for the expensive trailing-year asset response."""

    def __init__(self, cache_root: Path) -> None:
        self.path = Path(cache_root) / "trip-inputs" / "assets.json"

    def load(
        self,
        fingerprint: str,
        requested_range: DateRange,
        *,
        now: datetime | None = None,
    ) -> TripInputSnapshot | None:
        """Return a current matching snapshot, filtered to today's requested range."""
        current = _as_utc(now or datetime.now(tz=UTC))
        try:
            payload = json.loads(self.path.read_text())
            if payload["schema_version"] != _SCHEMA_VERSION:
                return None
            if payload["fingerprint"] != fingerprint:
                return None
            fetched_at = _as_utc(datetime.fromisoformat(payload["fetched_at"]))
            age = current - fetched_at
            if age < timedelta(0) or age > _MAX_AGE:
                return None
            source_start = datetime.fromisoformat(payload["source_start"]).date()
            if source_start > requested_range.start.date():
                return None
            source_end = datetime.fromisoformat(payload["source_end"]).date()
            if source_end < requested_range.end.date():
                return None
            assets = [Asset.model_validate(value) for value in payload["assets"]]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            logger.warning("Ignoring invalid trip input cache (%s)", type(error).__name__)
            return None

        start = requested_range.start.date()
        end = requested_range.end.date()
        matching = [asset for asset in assets if start <= asset.file_created_at.date() <= end]
        return TripInputSnapshot(fetched_at=fetched_at, assets=matching)

    def store(
        self,
        fingerprint: str,
        source_range: DateRange,
        assets: list[Asset],
        *,
        now: datetime | None = None,
    ) -> None:
        """Replace the snapshot atomically; a cache failure never breaks discovery."""
        payload: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "fetched_at": _as_utc(now or datetime.now(tz=UTC)).isoformat(),
            "source_start": source_range.start.isoformat(),
            "source_end": source_range.end.isoformat(),
            "asset_count": len(assets),
            "assets": [asset.model_dump(mode="json", by_alias=True) for asset in assets],
        }
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".assets-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Could not persist trip input cache (%s)", type(error).__name__)
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink()


def load_or_fetch_trip_assets(
    client: SyncImmichClient,
    *,
    cache_root: Path,
    server_url: str,
    buckets: list[TimeBucket],
    requested_range: DateRange,
    now: datetime | None = None,
) -> list[Asset]:
    """Reuse unchanged trip inputs, otherwise perform and persist one full fetch."""
    fingerprint = source_fingerprint(server_url, client.api_key, buckets)
    cache = TripInputCache(cache_root)
    snapshot = cache.load(fingerprint, requested_range, now=now)
    if snapshot is not None:
        try:
            recent = client.search_metadata(updated_after=snapshot.fetched_at, page=1, size=1)
        except Exception as error:
            logger.warning("Could not verify trip input cache freshness (%s)", type(error).__name__)
        else:
            if recent.total == 0:
                logger.info("Trip input cache hit: %d assets", len(snapshot.assets))
                return snapshot.assets
            logger.info("Trip input cache invalidated by newer Immich asset metadata")

    from immich_memories.api.all_assets_service import AllAssetsService

    source_range = DateRange(
        start=requested_range.start,
        end=requested_range.end + _MAX_AGE,
    )
    service = AllAssetsService(client._async_client.search)
    assets = client._run(service.get_assets_for_date_range(source_range))
    logger.info("Fetched %d assets for trip detection", len(assets))
    cache.store(fingerprint, source_range, assets, now=now)
    start = requested_range.start.date()
    end = requested_range.end.date()
    return [asset for asset in assets if start <= asset.file_created_at.date() <= end]
