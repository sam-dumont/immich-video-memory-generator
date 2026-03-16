#!/usr/bin/env python3
"""Preview trip titles and location cards for visual review.

Generates PNG images to /tmp/trip_preview/ for each scenario.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

OUTPUT_DIR = Path("/tmp/trip_preview")  # noqa: S108
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Locations ---
BRUSSELS = (50.8503, 4.3517)
OLERON = (45.9333, -1.3167)
OSTENDE = (51.2194, 2.9111)
CYPRUS_NICOSIA = (35.1856, 33.3823)
CYPRUS_PAPHOS = (34.7720, 32.4297)
CYPRUS_LARNACA = (34.9229, 33.6233)
CYPRUS_LIMASSOL = (34.6786, 33.0413)

# === 1. Trip intro titles ===
from immich_memories.titles._trip_titles import generate_trip_title  # noqa: E402

trips = [
    ("Brussels → Île d'Oléron", "Île d'Oléron, France", date(2025, 7, 12), date(2025, 7, 26)),
    ("Brussels → Ostende", "Ostende, Belgium", date(2025, 8, 15), date(2025, 8, 17)),
    ("Brussels → Cyprus", "Cyprus", date(2025, 9, 5), date(2025, 9, 15)),
    ("Short Cyprus", "Paphos, Cyprus", date(2025, 6, 20), date(2025, 6, 25)),
]

print("=" * 60)
print("TRIP TITLE TEXT GENERATION")
print("=" * 60)
for label, location, start, end in trips:
    title = generate_trip_title(location, start, end)
    days = (end - start).days
    print(f"\n  {label} ({days} days)")
    print(f"  → {title}")

# === 2. Render trip intro map frames ===
from immich_memories.titles.map_renderer import (  # noqa: E402
    render_location_card,
    render_trip_map_frame,
)

print("\n" + "=" * 60)
print("RENDERING MAP FRAMES (this fetches map tiles, may take a moment)")
print("=" * 60)

map_scenarios = [
    (
        "01_oleron_intro",
        "TWO WEEKS IN ÎLE D'OLÉRON, FRANCE, SUMMER 2025",
        [BRUSSELS, OLERON],
        ["Brussels", "Île d'Oléron"],
    ),
    (
        "02_ostende_intro",
        "A WEEKEND IN OSTENDE, BELGIUM, AUGUST 2025",
        [BRUSSELS, OSTENDE],
        ["Brussels", "Ostende"],
    ),
    (
        "03_cyprus_intro",
        "10 DAYS IN CYPRUS, SEPTEMBER 2025",
        [BRUSSELS, CYPRUS_NICOSIA, CYPRUS_PAPHOS, CYPRUS_LARNACA],
        ["Brussels", "Nicosia", "Paphos", "Larnaca"],
    ),
    (
        "04_cyprus_multi_stop",
        "10 DAYS IN CYPRUS, SEPTEMBER 2025",
        [CYPRUS_NICOSIA, CYPRUS_PAPHOS, CYPRUS_LARNACA, CYPRUS_LIMASSOL],
        ["Nicosia", "Paphos", "Larnaca", "Limassol"],
    ),
]

for filename, title, locations, names in map_scenarios:
    print(f"\n  Rendering {filename}...")
    img = render_trip_map_frame(locations, title, location_names=names)
    path = OUTPUT_DIR / f"{filename}.png"
    img.save(str(path))
    print(f"  → Saved to {path}")

# Also render portrait versions for the two main ones
for filename, title, locations, names in map_scenarios[:2]:
    print(f"\n  Rendering {filename}_portrait...")
    img = render_trip_map_frame(locations, title, width=1080, height=1920, location_names=names)
    path = OUTPUT_DIR / f"{filename}_portrait.png"
    img.save(str(path))
    print(f"  → Saved to {path}")

# === 3. Location interstitial cards ===
print("\n" + "=" * 60)
print("RENDERING LOCATION INTERSTITIAL CARDS")
print("=" * 60)

interstitials = [
    ("05_card_paphos", "Paphos", CYPRUS_PAPHOS[0], CYPRUS_PAPHOS[1]),
    ("06_card_nicosia", "Nicosia", CYPRUS_NICOSIA[0], CYPRUS_NICOSIA[1]),
    ("07_card_larnaca", "Larnaca", CYPRUS_LARNACA[0], CYPRUS_LARNACA[1]),
    ("08_card_oleron", "Île d'Oléron", OLERON[0], OLERON[1]),
    ("09_card_ostende", "Ostende", OSTENDE[0], OSTENDE[1]),
    ("10_card_no_gps", "Somewhere Nice", None, None),
]

for filename, name, lat, lon in interstitials:
    print(f"\n  Rendering {filename}...")
    img = render_location_card(name, lat=lat, lon=lon)
    path = OUTPUT_DIR / f"{filename}.png"
    img.save(str(path))
    print(f"  → Saved to {path}")

print("\n" + "=" * 60)
print(f"All previews saved to: {OUTPUT_DIR}")
print(f"Open with: open {OUTPUT_DIR}")
print("=" * 60)
