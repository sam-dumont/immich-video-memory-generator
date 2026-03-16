#!/usr/bin/env python3
"""Test overnight stop detection using the full pipeline.

1. Query ALL assets for a year
2. Run detect_trips() to find trips (filters out home)
3. For each trip, run detect_overnight_stops() on the trip assets
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import yaml

from immich_memories.analysis.trip_detection import (
    OvernightBase,
    detect_overnight_stops,
    detect_trips,
    haversine_km,
)
from immich_memories.api.models import Asset

config_path = Path.home() / ".immich-memories" / "config.yaml"
with open(config_path) as f:
    cfg = yaml.safe_load(f)

base_url = cfg["immich"]["url"].rstrip("/")
api_key = cfg["immich"]["api_key"]
headers = {"x-api-key": api_key}
http = httpx.Client(base_url=base_url, headers=headers, timeout=60)

# Homebase (Brussels)
HOME_LAT = 50.8468
HOME_LON = 4.3525

# Test years
YEARS = [2010, 2014, 2019, 2020, 2021, 2022, 2023, 2024, 2025]


def fetch_all_assets(start: datetime, end: datetime) -> list[Asset]:
    """Fetch all assets in a date range, paginating through results."""
    all_assets: list[Asset] = []
    page = 1
    while True:
        resp = http.post(
            "/api/search/metadata",
            json={
                "takenAfter": start.strftime("%Y-%m-%dT00:00:00.000Z"),
                "takenBefore": end.strftime("%Y-%m-%dT23:59:59.000Z"),
                "size": 1000,
                "page": page,
                "withExif": True,
            },
        )
        data = resp.json()
        items = data.get("assets", {}).get("items", [])
        if not items:
            break
        for raw in items:
            try:
                all_assets.append(Asset.model_validate(raw))
            except Exception:
                pass
        if len(items) < 1000:
            break
        page += 1
    return all_assets


print("=" * 80)
print("OVERNIGHT STOP DETECTION — FULL PIPELINE")
print("=" * 80)

for year in YEARS:
    # 1. Fetch all assets for the year (with buffer)
    start = datetime(year - 1, 12, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 31, tzinfo=UTC)
    print(f"\nFetching assets for {year}...")
    all_assets = fetch_all_assets(start, end)
    print(f"  {len(all_assets)} total assets")

    # 2. Detect trips
    trips = detect_trips(all_assets, HOME_LAT, HOME_LON)
    # Filter to trips that overlap with the requested year
    trips = [t for t in trips if t.start_date.year == year or t.end_date.year == year]

    if not trips:
        print(f"  No trips detected for {year}")
        continue

    for trip in trips:
        # 3. Get the trip's assets (by ID)
        trip_asset_ids = set(trip.asset_ids)
        trip_assets = [a for a in all_assets if a.id in trip_asset_ids]

        # 4. Run overnight stop detection
        bases = detect_overnight_stops(trip_assets, merge_radius_km=5.0)

        print(f"\n  {'=' * 70}")
        print(f"  {trip.location_name} ({trip.start_date} to {trip.end_date})")
        print(f"  {trip.asset_count} assets -> {len(bases)} bases")
        print(f"  {'-' * 70}")

        prev_base: OvernightBase | None = None
        for i, base in enumerate(bases):
            dist_str = ""
            if prev_base:
                d = haversine_km(prev_base.lat, prev_base.lon, base.lat, base.lon)
                mode = "PAN" if d < 30 else "ZOOM"
                dist_str = f"  ({d:>6.1f} km, {mode})"

            nights_str = f"{base.nights}n"
            date_range = (
                f"{base.start_date}"
                if base.start_date == base.end_date
                else f"{base.start_date} to {base.end_date}"
            )
            print(
                f"    Base {i + 1}: {base.location_name:>25} | "
                f"{nights_str:>4} | {date_range}{dist_str}"
            )
            prev_base = base

print(f"\n{'=' * 80}")
print("DONE")
