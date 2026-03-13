"""Generate demo map images to preview all map rendering variants."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from immich_memories.titles.map_renderer import (
    render_location_card,
    render_trip_map_frame,
)

OUTPUT_DIR = Path(__file__).parent.parent / "demo_output" / "maps"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === Location datasets ===

SPAIN_TRIP = {
    "locations": [
        (41.3874, 2.1686),  # Barcelona
        (39.4699, -0.3763),  # Valencia
        (37.3891, -5.9845),  # Seville
        (36.7213, -4.4214),  # Malaga
        (40.4168, -3.7038),  # Madrid
    ],
    "names": ["Barcelona", "Valencia", "Seville", "Malaga", "Madrid"],
}

EUROPE_MULTI = {
    "locations": [
        (48.8566, 2.3522),  # Paris
        (41.3874, 2.1686),  # Barcelona
        (41.9028, 12.4964),  # Rome
        (52.5200, 13.4050),  # Berlin
        (51.5074, -0.1278),  # London
        (59.3293, 18.0686),  # Stockholm
    ],
    "names": ["Paris", "Barcelona", "Rome", "Berlin", "London", "Stockholm"],
}

JAPAN_TRIP = {
    "locations": [
        (35.6762, 139.6503),  # Tokyo
        (34.6937, 135.5023),  # Osaka
        (35.0116, 135.7681),  # Kyoto
        (33.5904, 130.4017),  # Fukuoka
    ],
    "names": ["Tokyo", "Osaka", "Kyoto", "Fukuoka"],
}

FRANCE_OLERON = {
    "locations": [
        (46.0470, -1.4110),  # Phare de Chassiron (north tip)
        (46.0131, -1.3536),  # La-Brée-les-Bains
        (45.9950, -1.4100),  # Saint-Pierre-d'Oléron
        (45.9610, -1.3870),  # Dolus-d'Oléron
        (45.8870, -1.2050),  # Le Château-d'Oléron (south)
    ],
    "names": ["Chassiron", "La Brée", "Saint-Pierre", "Dolus", "Le Château"],
}


def gen(name: str, **kwargs):
    """Generate and save a map frame."""
    print(f"  Generating {name}...")
    img = render_trip_map_frame(**kwargs)
    path = OUTPUT_DIR / f"{name}.png"
    img.convert("RGB").save(path)
    print(f"  -> {path} ({img.size[0]}x{img.size[1]})")
    return path


def gen_card(name: str, **kwargs):
    """Generate and save a location card."""
    print(f"  Generating {name}...")
    img = render_location_card(**kwargs)
    path = OUTPUT_DIR / f"{name}.png"
    img.convert("RGB").save(path)
    print(f"  -> {path} ({img.size[0]}x{img.size[1]})")
    return path


if __name__ == "__main__":
    print("=== Map Frame Demos (satellite default) ===\n")

    # 1. Landscape - Spain, satellite
    print("[1] Landscape - Spain (satellite, 5 cities)")
    gen(
        "01_landscape_spain_satellite",
        locations=SPAIN_TRIP["locations"],
        location_names=SPAIN_TRIP["names"],
        title_text="TWO WEEKS IN SPAIN, SUMMER 2025",
        width=1920,
        height=1080,
    )

    # 2. Portrait - Spain, satellite
    print("[2] Portrait - Spain (satellite)")
    gen(
        "02_portrait_spain_satellite",
        locations=SPAIN_TRIP["locations"],
        location_names=SPAIN_TRIP["names"],
        title_text="TWO WEEKS IN SPAIN, SUMMER 2025",
        width=1080,
        height=1920,
    )

    # 3. Single location
    print("[3] Landscape - Single location (Île d'Oléron)")
    gen(
        "03_landscape_single_oleron",
        locations=FRANCE_OLERON["locations"],
        location_names=FRANCE_OLERON["names"],
        title_text="A WEEK IN ÎLE D'OLÉRON, JULY 2025",
        width=1920,
        height=1080,
    )

    # 4. Europe multi-country
    print("[4] Landscape - Europe tour (6 cities)")
    gen(
        "04_landscape_europe_satellite",
        locations=EUROPE_MULTI["locations"],
        location_names=EUROPE_MULTI["names"],
        title_text="A MONTH ACROSS EUROPE, SUMMER 2025",
        width=1920,
        height=1080,
    )

    # 5. Portrait - Europe
    print("[5] Portrait - Europe tour")
    gen(
        "05_portrait_europe_satellite",
        locations=EUROPE_MULTI["locations"],
        location_names=EUROPE_MULTI["names"],
        title_text="A MONTH ACROSS EUROPE",
        width=1080,
        height=1920,
    )

    # 6. Japan
    print("[6] Landscape - Japan (4 cities)")
    gen(
        "06_landscape_japan_satellite",
        locations=JAPAN_TRIP["locations"],
        location_names=JAPAN_TRIP["names"],
        title_text="10 DAYS IN JAPAN, MARCH 2025",
        width=1920,
        height=1080,
    )

    # 7. OSM style for comparison
    print("[7] Landscape - Spain (OSM style)")
    gen(
        "07_landscape_spain_osm",
        locations=SPAIN_TRIP["locations"],
        location_names=SPAIN_TRIP["names"],
        title_text="TWO WEEKS IN SPAIN, SUMMER 2025",
        width=1920,
        height=1080,
        map_style="osm",
    )

    # === Location Cards ===
    print("\n=== Location Card Demos ===\n")

    gen_card(
        "card_landscape_barcelona",
        location_name="Barcelona",
        width=1920,
        height=1080,
        lat=41.3874,
        lon=2.1686,
    )
    gen_card(
        "card_portrait_tokyo",
        location_name="Tokyo",
        width=1080,
        height=1920,
        lat=35.6762,
        lon=139.6503,
    )

    print(f"\nAll demos saved to: {OUTPUT_DIR}")
    print(f"Total files: {len(list(OUTPUT_DIR.glob('*.png')))}")
