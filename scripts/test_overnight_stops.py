#!/usr/bin/env python3
"""Test overnight stop detection on REAL Immich album data.

Fetches all albums with a year in the name, runs detect_overnight_stops(),
and shows how the algorithm segments each trip.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import yaml

from immich_memories.analysis.trip_detection import (
    OvernightBase,
    detect_overnight_stops,
    haversine_km,
)

# Load config
config_path = Path.home() / ".immich-memories" / "config.yaml"
with open(config_path) as f:
    cfg = yaml.safe_load(f)

base_url = cfg["immich"]["url"].rstrip("/")
headers = {"x-api-key": cfg["immich"]["api_key"]}
http = httpx.Client(base_url=base_url, headers=headers, timeout=30)

# Fetch all albums
albums = http.get("/api/albums").json()

# Filter to holiday albums (name contains a 4-digit year)
holiday = [a for a in albums if re.search(r"\b2\d{3}\b", a.get("albumName", ""))]
holiday.sort(key=lambda a: a["albumName"])

print(f"Found {len(holiday)} holiday albums")
print("=" * 80)

for album_info in holiday:
    name = album_info["albumName"]
    album_id = album_info["id"]
    count = album_info.get("assetCount", "?")

    # Fetch full album with assets
    album_data = http.get(f"/api/albums/{album_id}").json()
    raw_assets = album_data.get("assets", [])

    if not raw_assets:
        print(f"\n{name} ({count} assets): NO ASSETS")
        continue

    # Convert to Asset objects
    from immich_memories.api.models import Asset

    assets = []
    for raw in raw_assets:
        try:
            assets.append(Asset.model_validate(raw))
        except Exception:
            pass

    if not assets:
        print(f"\n{name}: could not parse assets")
        continue

    # Run overnight stop detection
    bases = detect_overnight_stops(assets, merge_radius_km=5.0)

    print(f"\n{'=' * 80}")
    print(f"{name} ({len(assets)} assets) -> {len(bases)} bases")
    print("-" * 80)

    if not bases:
        print("  No GPS-tagged assets found")
        continue

    prev_base: OvernightBase | None = None
    for i, base in enumerate(bases):
        dist_str = ""
        if prev_base:
            d = haversine_km(prev_base.lat, prev_base.lon, base.lat, base.lon)
            mode = "PAN" if d < 30 else "ZOOM"
            dist_str = f"  ({d:>6.1f} km, {mode})"

        nights_str = f"{base.nights} night{'s' if base.nights > 1 else ''}"
        date_range = (
            f"{base.start_date}"
            if base.start_date == base.end_date
            else f"{base.start_date} to {base.end_date}"
        )
        print(
            f"  Base {i + 1}: {base.location_name:>25} | "
            f"{nights_str:>9} | {date_range} | "
            f"({base.lat:.3f}, {base.lon:.3f}){dist_str}"
        )
        prev_base = base

    # Summary
    total_nights = sum(b.nights for b in bases)
    multi_night = [b for b in bases if b.nights > 1]
    single_night = [b for b in bases if b.nights == 1]
    print(
        f"  Summary: {total_nights} nights, {len(multi_night)} multi-night bases, "
        f"{len(single_night)} single-night stops"
    )

print(f"\n{'=' * 80}")
print("DONE")
