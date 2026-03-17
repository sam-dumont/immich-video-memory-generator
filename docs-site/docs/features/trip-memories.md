---
sidebar_position: 12
title: Trip Memories
---

# Trip Memories

A trip memory is a structured video that follows the shape of a trip: animated map intro, clips distributed across each overnight stop, location cards between segments, and a title generated from your GPS data.

![Trip preset selection](../screenshots/trip-preset.png)

## Creating a trip memory

In Step 1, select the **Trip** preset. The app will:

1. Show all detected trips from the selected time period (each trip is a separate detected journey with at least 2 days away from home)
2. Let you pick which trip to use
3. Run GPS filtering, overnight detection, and LLM title generation before you reach Step 3

## What happens behind the scenes

**GPS filtering**: clips within 50 km of home get removed. The assumption is you don't want footage of your commute mixed into a trip about Tuscany.

**Overnight detection**: the algorithm clusters GPS data to find where you slept each night, then groups them into `OvernightBase` segments. See [Trip Detection](./trip-detection.md) for how this works.

**Map animation**: using your home coordinates and the trip's destination(s), a satellite fly-over animation is generated as the opening title screen. Long distances get the van Wijk zoom; short hops get a smooth pan.

**LLM title**: raw GPS clusters (daily location + photo count) go to your configured `title_llm`. It produces a title, optional subtitle, trip type classification, and a map mode recommendation. All editable in Step 3.

## Clip distribution by segment

Clips get distributed across overnight bases proportionally by night count. A 3-night stay in Lyon gets roughly 3x the clips of a 1-night stop in Valence.

Each segment gets at least 1 clip. The total clip budget comes from your target duration setting.

Example for a 10-minute memory with 4 segments (1 night, 3 nights, 5 nights, 1 night):

| Segment | Nights | Clip budget |
|---------|--------|------------|
| Paris (depart) | 1 | 1 |
| Lyon | 3 | 4 |
| Provence | 5 | 6 |
| Marseille | 1 | 1 |

## Step 3: reviewing the title

After analysis completes, Step 3 shows the LLM-generated title and subtitle. You can edit both fields directly. There's also a regenerate button if you want to try again with the same GPS data.

![Trip Step 3 with title](../screenshots/trip-step3-title.png)

The trip type classification and map mode are shown below the title fields. Changing the map mode updates which style of map animation gets rendered.

## Requirements

- `trips.homebase_latitude` and `trips.homebase_longitude` must be set in your config
- A `title_llm` should be configured for title generation (falls back to `llm` if not set, or template title if neither is set)
- GPS must be present in most of the album's assets for overnight detection to work well
