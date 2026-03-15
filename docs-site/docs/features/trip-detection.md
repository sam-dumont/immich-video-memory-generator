---
sidebar_position: 11
title: Trip Detection
---

# Trip Detection

The trip detection system figures out where you slept each night of a trip, then uses that to structure the memory: clip distribution, location cards, and the animated map intro.

![Trip detection results](../screenshots/trip-detection.png)

## How it works

Trip detection runs a 4-phase algorithm against GPS-tagged assets from the album. It needs `home_lat` / `home_lon` set in your config — that's the reference point for deciding what counts as "away".

### Phase 1: Cluster + identify home bases

All GPS-tagged photos get clustered by location (greedy spatial clustering, 15 km radius by default). A cluster qualifies as a "home base" if it shows up on at least 30% of the trip's days, or if there's a multi-day gap in its photo dates (the return-gap heuristic: you left and came back).

Home bases are the places you slept. Excursions are day trips that don't meet that threshold.

### Phase 2: Assign days to stops

For each day in the trip, the algorithm looks at where you took photos at the end of the day (the last GPS-tagged photo). If that location matches a known home base, the day gets tagged to that base. If it doesn't, it gets tagged to wherever the next day starts — the idea being that's where you were heading to sleep.

### Phase 3: Merge consecutive same-location nights

Consecutive days tagged to the same location (within 5 km) collapse into a single `OvernightBase` entry. A week in one city becomes one base with `nights=7`, not 7 separate entries.

### Phase 4: Merge non-consecutive repeated bases

Handles the case where you leave a base and come back: `Paris → Lyon → Paris` collapses into a single Paris base that spans the whole trip. The merge loop repeats until stable.

## Trip types

After detecting overnight bases, the LLM (or the trip detection logic) classifies the pattern:

| Type | Pattern | Example |
|------|---------|---------|
| `base_camp` | Same base every night, excursions during the day | Val d'Aoste: base in Ville Sur Sarre, day hikes to Cogne and La Thuile |
| `multi_base` | 2-3 bases, each for multiple consecutive nights | Cyprus: 5 nights Nicosia, 5 nights Geroskipou |
| `road_trip` | Different location each day, large distances | Italy coast to coast, 14 stops |
| `hiking_trail` | Progressive daily moves, short distances | Saxon Switzerland: Hohnstein → Bad Schandau → Königstein |

## Map mode recommendations

Trip type influences how the animated map intro behaves:

- `base_camp` → `excursions` map mode (shows home base + day-trip pins radiating out)
- `multi_base`, `road_trip`, `hiking_trail` → `overnight_stops` map mode (shows each base as a stop)

The LLM produces a `map_mode` field alongside the title suggestion. You can override it in Step 3.

## Configuration

```yaml
home:
  lat: 48.8566    # Your home coordinates
  lon: 2.3522

trip_detection:
  min_distance_km: 50     # Minimum distance from home to count as "away"
  min_duration_days: 2    # Minimum trip length
  max_gap_days: 2         # Max days without photos before splitting into separate trips
  merge_radius_km: 5.0    # Distance within which consecutive nights merge
```

## Requirements

GPS data must be present in the EXIF of your photos. If the album has few GPS-tagged assets, detection will be incomplete. The algorithm skips assets with no GPS rather than failing — it just works with what's there.

The overnight detection also needs access to all assets for the trip period, not just the album clips that made it through scoring. In the UI, this happens automatically when you select the trip preset. From the CLI, pass the full date range covering the trip.
