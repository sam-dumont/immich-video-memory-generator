#!/usr/bin/env python3
"""Test LLM title generation on real trip data at various temperatures.

Usage: .venv/bin/python scripts/test_llm_titles.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from immich_memories.analysis.trip_detection import (  # noqa: E402
    OvernightBase,
    detect_overnight_stops,
    detect_trips,
)
from immich_memories.config_models import LLMConfig  # noqa: E402
from immich_memories.analysis.llm_query import query_llm  # noqa: E402
from immich_memories.titles.llm_titles import (  # noqa: E402
    build_title_prompt,
    generate_title_with_llm,
    parse_title_response,
)

# Load config
cfg = yaml.safe_load(open(Path.home() / ".immich-memories" / "config.yaml"))  # noqa: SIM115, S108, PTH123
IMMICH_URL = cfg["immich"]["url"]
API_KEY = cfg["immich"]["api_key"]
LLM_CFG = LLMConfig(
    provider=cfg["llm"]["provider"],
    base_url=cfg["llm"]["base_url"],
    model=cfg["llm"]["model"],
    api_key=cfg["llm"].get("api_key", ""),
)
LOCALE = cfg.get("title_screens", {}).get("locale", "fr")
HOME_LAT = cfg["trips"]["homebase_latitude"]
HOME_LON = cfg["trips"]["homebase_longitude"]

HEADERS = {"x-api-key": API_KEY}
TEMPERATURES = [0.1, 0.5, 1.0]


def fetch_assets(year: int) -> list[dict]:
    """Fetch all assets for a year with pagination."""
    all_assets = []
    page = 1
    while True:
        r = httpx.post(
            f"{IMMICH_URL}/api/search/metadata",
            json={
                "takenAfter": f"{year}-01-01T00:00:00Z",
                "takenBefore": f"{year + 1}-01-01T00:00:00Z",
                "page": page,
                "size": 1000,
                "withExif": True,
            },
            headers=HEADERS,
            timeout=30,
        )
        items = r.json().get("assets", {}).get("items", [])
        if not items:
            break
        all_assets.extend(items)
        page += 1
    return all_assets


def api_to_asset(raw: dict):
    """Convert raw API dict to Asset model."""
    from datetime import UTC, datetime

    from immich_memories.api.models import Asset, AssetType, ExifInfo

    exif_raw = raw.get("exifInfo", {}) or {}
    exif = ExifInfo(
        latitude=exif_raw.get("latitude"),
        longitude=exif_raw.get("longitude"),
        city=exif_raw.get("city"),
        country=exif_raw.get("country"),
    )
    ldt_str = raw.get("localDateTime")
    ldt = datetime.fromisoformat(ldt_str.replace("Z", "+00:00")) if ldt_str else None
    ts_str = raw.get("fileCreatedAt", raw.get("createdAt", "2020-01-01T00:00:00Z"))
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return Asset(
        id=raw["id"],
        type=AssetType.VIDEO if raw.get("type") == "VIDEO" else AssetType.IMAGE,
        fileCreatedAt=ts,
        fileModifiedAt=ts,
        updatedAt=ts,
        localDateTime=ldt,
        exifInfo=exif,
    )


def build_locations_list(bases: list[OvernightBase]) -> list[str]:
    return [f"{b.location_name} ({b.nights} nights)" for b in bases]


def build_overnight_summary(bases: list[OvernightBase]) -> str:
    multi_night = [b for b in bases if b.nights > 1]
    single_night = [b for b in bases if b.nights == 1]
    return f"{len(multi_night)} home bases, {len(single_night)} excursions/stops"


def get_country(bases: list[OvernightBase], assets) -> str | None:
    """Get dominant country from assets."""
    from collections import Counter

    countries = Counter(
        a.exif_info.country for a in assets if a.exif_info and a.exif_info.country
    )
    if countries:
        return countries.most_common(1)[0][0]
    return None


async def test_trip(trip_name: str, assets, bases: list[OvernightBase]) -> None:
    """Run title generation at multiple temperatures for one trip."""
    if not bases:
        print(f"  No bases detected, skipping")
        return

    start = bases[0].start_date
    end = bases[-1].end_date
    duration = (end - start).days + 1
    locations = build_locations_list(bases)
    summary = build_overnight_summary(bases)
    country = get_country(bases, assets)

    print(f"\n{'=' * 70}")
    print(f"  {trip_name}")
    print(f"  {start} to {end} ({duration} days)")
    print(f"  Bases: {locations}")
    print(f"  Pattern: {summary}")
    print(f"  Country: {country}")
    print(f"  Locale: {LOCALE}")
    print(f"{'=' * 70}")

    for temp in TEMPERATURES:
        try:
            await asyncio.sleep(1)  # avoid hammering the LLM server
            prompt = build_title_prompt(
                memory_type="trip", locale=LOCALE,
                start_date=str(start), end_date=str(end),
                duration_days=duration, locations=locations,
                country=country, overnight_summary=summary,
            )
            raw = await query_llm(prompt, LLM_CFG, temperature=temp)
            result = parse_title_response(raw)
            if result:
                mode = f" [{result.trip_type}/{result.map_mode}]" if result.trip_type else ""
                sub = f' / "{result.subtitle}"' if result.subtitle else ""
                print(f"  T={temp:.1f}: \"{result.title}\"{sub}{mode}")
                if result.map_mode_reason:
                    print(f"         reason: {result.map_mode_reason}")
            else:
                print(f"  T={temp:.1f}: PARSE FAIL — raw: {raw[:150] if raw else 'None'}")
        except Exception as e:
            print(f"  T={temp:.1f}: ERROR — {e}")


async def test_person_memory() -> None:
    """Test a person memory title."""
    print(f"\n{'=' * 70}")
    print(f"  PERSON MEMORY: Alice & Emile")
    print(f"{'=' * 70}")

    for temp in TEMPERATURES:
        try:
            prompt = build_title_prompt(
                memory_type="person", locale=LOCALE,
                start_date="2019-01-01", end_date="2025-12-31",
                duration_days=2556, person_names=["Alice", "Emile"],
            )
            raw = await query_llm(prompt, LLM_CFG, temperature=temp)
            result = parse_title_response(raw)
            sub = f' / "{result.subtitle}"' if result and result.subtitle else ""
            print(f"  T={temp:.1f}: \"{result.title}\"{sub}" if result else f"  T={temp:.1f}: PARSE FAIL — {raw[:100] if raw else 'None'}")
        except Exception as e:
            print(f"  T={temp:.1f}: ERROR — {e}")


async def test_year_memory() -> None:
    """Test a year memory title."""
    print(f"\n{'=' * 70}")
    print(f"  YEAR MEMORY: 2024")
    print(f"{'=' * 70}")

    for temp in TEMPERATURES:
        try:
            prompt = build_title_prompt(
                memory_type="year", locale=LOCALE,
                start_date="2024-01-01", end_date="2024-12-31",
                duration_days=366, clip_descriptions=[
                    "family at the beach", "hiking in mountains",
                    "birthday party with candles", "sunset over the sea",
                ],
            )
            raw = await query_llm(prompt, LLM_CFG, temperature=temp)
            result = parse_title_response(raw)
            sub = f' / "{result.subtitle}"' if result and result.subtitle else ""
            print(f"  T={temp:.1f}: \"{result.title}\"{sub}" if result else f"  T={temp:.1f}: PARSE FAIL — {raw[:100] if raw else 'None'}")
        except Exception as e:
            print(f"  T={temp:.1f}: ERROR — {e}")


async def main():
    print("=" * 70)
    print("LLM TITLE GENERATION — REAL DATA TEST")
    print(f"Model: {LLM_CFG.model} @ {LLM_CFG.base_url}")
    print(f"Locale: {LOCALE}")
    print(f"Temperatures: {TEMPERATURES}")
    print("=" * 70)

    # Test years with trips
    years = [2014, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

    for year in years:
        print(f"\nFetching assets for {year}...")
        raw_assets = fetch_assets(year)
        if not raw_assets:
            print(f"  No assets found for {year}")
            continue

        assets = [api_to_asset(r) for r in raw_assets]
        print(f"  {len(assets)} total assets")

        trips = detect_trips(assets, HOME_LAT, HOME_LON)
        if not trips:
            print(f"  No trips detected for {year}")
            continue

        for trip in trips:
            trip_assets = [a for a in assets if a.id in set(trip.asset_ids)]
            bases = detect_overnight_stops(trip_assets)
            await test_trip(
                f"{trip.location_name} ({year})",
                trip_assets,
                bases,
            )

    # Non-trip memories
    await test_person_memory()
    await test_year_memory()

    print(f"\n{'=' * 70}")
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
