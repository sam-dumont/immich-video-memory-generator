---
sidebar_position: 9
title: Map Animation
---

# Map Animation

Trip memories start with an animated satellite fly-over from your home location to the destination. It's a Google Earth-style zoom that gives context before the clips start.

![Map animation frame](../screenshots/map-animation-frame.png)

## What it does

The animation starts at city-level zoom on your home location, flies out and across to the destination(s), then settles at a zoom level that shows all the destination pins. Duration is configurable — default is 5 seconds.

Each endpoint gets a pin (red circle with white outline) and a city label. The title text fades in over the satellite imagery, sitting in the lower third so it doesn't block the map.

## Zoom vs. pan selection

Two animation modes get picked automatically based on how far apart departure and destination are:

- **Van Wijk zoom**: for long distances. Zooms out to show the route, then zooms back in. The math is the d3 `interpolateZoom` algorithm — it picks the smoothest path through zoom-space rather than just linearly interpolating.
- **Linear pan**: for short hops. When the destination is close enough that the mid-transit zoom would stay above zoom level 10 (roughly city-block level), it just pans at fixed zoom instead of zooming out unnecessarily.

![Map pan close distance](../screenshots/map-pan-close.png)

## Configuration

Map animation settings live under `title_screens`:

```yaml
trips:
  homebase_latitude: 48.8566    # Your home coordinates
  homebase_longitude: 2.3522
```

Home coordinates come from the `trips` config section and get passed through automatically when using the trip preset. Destination pins are derived from the overnight detection algorithm: you don't configure them manually.

### Tile source

Satellite imagery comes from ArcGIS World Imagery tiles — no API key required. Tiles are cached in-memory during rendering to avoid redundant fetches across adjacent frames.

## Map styles

The static map renderer supports three styles, used for pin-and-label map frames (not the fly-over animation):

| Key | Source | Notes |
|-----|--------|-------|
| `satellite` | ArcGIS World Imagery | Default. Used for the fly-over. |
| `osm` | OpenStreetMap | Clean street map, good for city-level |
| `topo` | OpenTopoMap | Topographic, useful for hiking trips |

Configure with `map_style: osm` under `title_screens` if you want a different style for location cards.
