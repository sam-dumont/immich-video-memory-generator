#!/usr/bin/env python3
"""Preview: Saxon Switzerland / Berlin 2022 — ALL in PORTRAIT (1080x1920).

Based on real Immich album "Germanie 2022" GPS data:
  Day 1: Brussels → Dresden → Lohmen/Hohnstein (start of Malerweg)
  Day 2: Hohnstein → Porschdorf → Rathmannsdorf
  Day 3: Bad Schandau → Sebnitz
  Day 4: Bad Schandau → Mikulášovice (Czech Republic!)
  Day 5: Bad Schandau → Kurort Gohrisch
  Day 6: Kurort Gohrisch → Königstein → Stadt Wehlen
  Day 7-10: Berlin (Kreuzberg, Mitte, Tiergarten, Friedrichshain)

Demonstrates:
  1. Intro fly: Brussels → overview of ALL stops (Saxon Switzerland + Berlin)
  2. Day-by-day location cards (between hiking days)
  3. Journey recap: full A→B→C→D route fly-over
"""

from __future__ import annotations

import time
from pathlib import Path

OUTPUT_DIR = Path("/tmp/trip_saxon_preview")  # noqa: S108
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Locations (centroids per day from Immich GPS data) ---
BRUSSELS = (50.8503, 4.3517)
DRESDEN = (51.0568, 13.7142)
LOHMEN = (50.9950, 13.9731)
HOHNSTEIN = (50.9782, 14.1085)
PORSCHDORF = (50.9552, 14.1290)
BAD_SCHANDAU = (50.9155, 14.2000)
SEBNITZ = (50.9600, 14.2800)
MIKULASOVICE = (50.9700, 14.3600)  # Czech Republic!
GOHRISCH = (50.8985, 14.1517)
KONIGSTEIN = (50.9186, 14.0706)
BERLIN = (52.5146, 13.3970)

# All hiking stops (for the overview intro)
ALL_STOPS = [DRESDEN, HOHNSTEIN, BAD_SCHANDAU, SEBNITZ, GOHRISCH, KONIGSTEIN, BERLIN]
ALL_NAMES = ["Dresden", "Hohnstein", "Bad Schandau", "Sebnitz", "Gohrisch", "Königstein", "Berlin"]

from immich_memories.titles.map_animation import create_map_fly_video  # noqa: E402

W, H = 1080, 1920  # PORTRAIT

print("=" * 60)
print("SAXON SWITZERLAND + BERLIN 2022 — ALL PORTRAIT")
print("=" * 60)

scenarios = [
    # 1. INTRO: Brussels > overview of ALL stops
    (
        "01_intro_overview",
        "10 DAYS IN SAXONY & BERLIN, APRIL 2022",
        BRUSSELS,
        ALL_STOPS,
        ALL_NAMES,
        10.0,
    ),
    # 2. Day 1: Arriving at the start of the Malerweg
    (
        "02_day1_arrive",
        "DAY 1 -- DRESDEN TO HOHNSTEIN",
        BRUSSELS,
        [DRESDEN, HOHNSTEIN],
        ["Dresden", "Hohnstein"],
        6.0,
    ),
    # 3. Day-to-day hiking transitions (short hops → should PAN)
    (
        "03_day2_hohnstein_altendorf",
        "DAY 2 -- HOHNSTEIN TO ALTENDORF",
        HOHNSTEIN,
        [PORSCHDORF],
        ["Altendorf"],
        4.0,
    ),
    (
        "04_day3_bad_schandau",
        "DAY 3 -- BAD SCHANDAU",
        PORSCHDORF,
        [BAD_SCHANDAU],
        ["Bad Schandau"],
        4.0,
    ),
    (
        "05_day4_czech_excursion",
        "DAY 4 -- INTO CZECH REPUBLIC",
        BAD_SCHANDAU,
        [MIKULASOVICE],
        ["Mikulasovice"],
        5.0,
    ),
    (
        "06_day5_gohrisch",
        "DAY 5 -- KURORT GOHRISCH",
        BAD_SCHANDAU,
        [GOHRISCH],
        ["Kurort Gohrisch"],
        4.0,
    ),
    (
        "07_day6_konigstein",
        "DAY 6 -- KONIGSTEIN",
        GOHRISCH,
        [KONIGSTEIN],
        ["Konigstein"],
        4.0,
    ),
    # 4. Travel to Berlin
    (
        "08_to_berlin",
        "OFF TO BERLIN",
        KONIGSTEIN,
        [BERLIN],
        ["Berlin"],
        6.0,
    ),
    # 5. Recap: full route with ALL stops visible
    (
        "09_recap_full_route",
        "BRUSSELS TO SAXONY TO BERLIN",
        BRUSSELS,
        ALL_STOPS,
        ALL_NAMES,
        8.0,
    ),
]

total_time = 0.0
for filename, title, departure, dests, names, dur in scenarios:
    print(f"\n  {filename}: {title}")
    print(f"    {len(dests)} dest(s), {dur}s portrait")
    path = OUTPUT_DIR / f"{filename}.mp4"
    t0 = time.time()
    create_map_fly_video(
        departure=departure,
        destinations=dests,
        title_text=title,
        output_path=path,
        width=W,
        height=H,
        duration=dur,
        fps=30.0,
        hold_start=0.5,
        hold_end=1.5,
        hdr=False,
        destination_names=names,
    )
    elapsed = time.time() - t0
    total_time += elapsed
    frames = int(dur * 30)
    print(f"    → {path}  [{elapsed:.1f}s, {frames}f, {elapsed / frames:.2f}s/f]")

print(f"\n{'=' * 60}")
print(f"Total: {total_time:.1f}s for {len(scenarios)} videos")
print(f"All saved to: {OUTPUT_DIR}")
print(f"open {OUTPUT_DIR}")
print("=" * 60)
