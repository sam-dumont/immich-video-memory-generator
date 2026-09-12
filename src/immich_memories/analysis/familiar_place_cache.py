"""Private, credential-scoped GPS history; warm captions never scan the library."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.familiar_places import (
    PlaceHistory,
    PlaceObservation,
    valid_coordinates,
)

if TYPE_CHECKING:
    from immich_memories.api.sync_client import SyncImmichClient

logger = logging.getLogger(__name__)
_MAX_AGE = timedelta(days=7)


def _read(path: Path, now: datetime) -> PlaceHistory | None:
    try:
        payload = json.loads(path.read_text())
        age = now - datetime.fromisoformat(payload["fetched_at"])
        if payload["schema_version"] != 1 or not timedelta(0) <= age <= _MAX_AGE:
            return None
        return PlaceHistory(
            [
                PlaceObservation(float(lat), float(lon), date.fromisoformat(day), str(country))
                for lat, lon, day, country in payload["observations"]
            ]
        )
    except (OSError, KeyError, ValueError, TypeError):
        return None


def _write(path: Path, observations: list[PlaceObservation], now: datetime) -> None:
    payload = {
        "schema_version": 1,
        "fetched_at": now.isoformat(),
        "observations": [
            [row.latitude, row.longitude, row.day.isoformat(), row.country] for row in observations
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # NamedTemporaryFile creates mode 0600, retained through the atomic rename.
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, separators=(",", ":"))
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def load_place_history(client: SyncImmichClient, cache_root: Path) -> PlaceHistory:
    """Fetch all accessible dated GPS metadata at most weekly, never the pixels.

    The key separates both servers and API-key scopes. A failed or partial
    fetch is never cached. Deleting `familiar-places/` forces an early refresh.
    """
    identity = json.dumps([client.base_url.rstrip("/"), client.api_key]).encode()
    fingerprint = hashlib.sha256(identity).hexdigest()
    path = cache_root / "familiar-places" / f"{fingerprint}.json"
    now = datetime.now(UTC)
    cached = _read(path, now)
    if cached is not None:
        return cached
    logger.info("Preparing familiar places from library GPS history (cached for seven days)")
    observations: set[PlaceObservation] = set()
    page = 1
    while True:
        result = client.search_metadata(page=page, size=1000)
        for asset in result.all_assets:
            exif = asset.exif_info
            if exif is not None and valid_coordinates(exif.latitude, exif.longitude):
                assert exif.latitude is not None and exif.longitude is not None
                observations.add(
                    PlaceObservation(
                        float(exif.latitude),
                        float(exif.longitude),
                        asset.file_created_at.date(),
                        exif.country or "",
                    )
                )
        if not result.next_page:
            break
        next_page = int(result.next_page)
        if next_page <= page:
            raise ValueError("Immich GPS history pagination did not advance")
        page = next_page
    rows = sorted(observations, key=lambda row: (row.day, row.latitude, row.longitude, row.country))
    try:
        _write(path, rows, now)
    except OSError:
        logger.warning(
            "Could not cache familiar places; this render still uses the fetched history"
        )
    return PlaceHistory(rows)
