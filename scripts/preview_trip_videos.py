#!/usr/bin/env python3
"""Preview trip title VIDEOS — map fly animation.

Two modes:
1. INTRO FLY: departure → destination overview (shows all pins)
2. JOURNEY FLY: A→B→C→D multi-segment route (future, not yet implemented)
"""

from __future__ import annotations

import time
from pathlib import Path

OUTPUT_DIR = Path("/tmp/trip_video_preview3")  # noqa: S108
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Locations ---
BRUSSELS = (50.8503, 4.3517)
OLERON = (45.9333, -1.3167)
OSTENDE = (51.2194, 2.9111)
CYPRUS_NICOSIA = (35.1856, 33.3823)
CYPRUS_PAPHOS = (34.7720, 32.4297)
CYPRUS_LARNACA = (34.9229, 33.6233)

from immich_memories.titles.map_animation import create_map_fly_video  # noqa: E402

print("=" * 60)
print("INTRO FLY ANIMATIONS (departure → destination overview)")
print("=" * 60)

scenarios = [
    (
        "01_intro_cyprus",
        "10 DAYS IN CYPRUS, SEPTEMBER 2025",
        BRUSSELS,
        [CYPRUS_NICOSIA, CYPRUS_PAPHOS, CYPRUS_LARNACA],
        ["Nicosia", "Paphos", "Larnaca"],
        8.0,  # long distance → slower
    ),
    (
        "02_intro_oleron",
        "TWO WEEKS IN ÎLE D'OLÉRON, SUMMER 2025",
        BRUSSELS,
        [OLERON],
        ["Île d'Oléron"],
        6.0,
    ),
    (
        "03_intro_ostende",
        "A WEEKEND IN OSTENDE, AUGUST 2025",
        BRUSSELS,
        [OSTENDE],
        ["Ostende"],
        4.0,  # short distance → faster is fine
    ),
    (
        "04_intro_cyprus_close",
        "10 DAYS IN CYPRUS, SEPTEMBER 2025",
        CYPRUS_NICOSIA,
        [CYPRUS_PAPHOS, CYPRUS_LARNACA],
        ["Paphos", "Larnaca"],
        5.0,  # already in Cyprus, short fly
    ),
]

# Portrait versions (most real videos are portrait from phones)
portrait_scenarios = [
    (
        "05_portrait_cyprus",
        "10 DAYS IN CYPRUS, SEPTEMBER 2025",
        BRUSSELS,
        [CYPRUS_NICOSIA, CYPRUS_PAPHOS, CYPRUS_LARNACA],
        ["Nicosia", "Paphos", "Larnaca"],
        8.0,
    ),
    (
        "06_portrait_ostende",
        "A WEEKEND IN OSTENDE, AUGUST 2025",
        BRUSSELS,
        [OSTENDE],
        ["Ostende"],
        4.0,
    ),
]

for filename, title, departure, dests, names, dur in scenarios:
    print(f"\n  {filename}: {title}")
    print(f"    From {departure} → {len(dests)} destinations, {dur}s")
    path = OUTPUT_DIR / f"{filename}.mp4"
    t0 = time.time()
    create_map_fly_video(
        departure=departure,
        destinations=dests,
        title_text=title,
        output_path=path,
        width=1920,
        height=1080,
        duration=dur,
        fps=30.0,
        hold_start=0.5,
        hold_end=1.5,
        hdr=False,
        destination_names=names,
    )
    elapsed = time.time() - t0
    frames = int(dur * 30)
    print(f"    → {path}  [{elapsed:.1f}s, {frames} frames, {elapsed / frames:.2f}s/frame]")

print("\n" + "=" * 60)
print("PORTRAIT MODE (1080x1920)")
print("=" * 60)

for filename, title, departure, dests, names, dur in portrait_scenarios:
    print(f"\n  {filename}: {title}")
    print(f"    Portrait, {dur}s")
    path = OUTPUT_DIR / f"{filename}.mp4"
    t0 = time.time()
    create_map_fly_video(
        departure=departure,
        destinations=dests,
        title_text=title,
        output_path=path,
        width=1080,
        height=1920,
        duration=dur,
        fps=30.0,
        hold_start=0.5,
        hold_end=1.5,
        hdr=False,
        destination_names=names,
    )
    elapsed = time.time() - t0
    frames = int(dur * 30)
    print(f"    → {path}  [{elapsed:.1f}s, {frames} frames, {elapsed / frames:.2f}s/frame]")

print("\n" + "=" * 60)
print(f"All videos saved to: {OUTPUT_DIR}")
print(f"open {OUTPUT_DIR}")
print("=" * 60)
