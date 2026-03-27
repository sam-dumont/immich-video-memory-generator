---
sidebar_position: 8
title: Title Screens & Maps
---

# Title Screens & Maps

This is where the output stops looking like "FFmpeg concat" and starts looking like something you'd actually want to show people.

Title screens are the structural connective tissue: animated intro cards, month dividers, trip maps, globe fly-overs, and ending sequences. They're what separates a clip dump from a memory video that feels produced. Same kind of polish as Relive, but running on your own hardware with your own data.

## What gets generated

Depending on the memory type, title screens include some or all of:

- **Intro card**: content-backed background (blurred + darkened frame from your footage), white title text with entrance animation, optional subtitle (person name, date range). 3.5 seconds.
- **Month dividers**: for yearly memories, each month section gets a divider card. Keeps the viewer oriented in a 10-minute video.
- **Trip map animation**: satellite fly-over from home to destination using Van Wijk zoom. Replaces the generic intro for trip memories.
- **Globe rendering**: for long-distance trips, a rotating 3D globe with departure and arrival points. Built in Taichi, runs on GPU when available.
- **Location cards**: city name + map thumbnail between trip segments.
- **Ending sequence**: fade-to-white with year or closing text.

## Three rendering backends

The system picks the best renderer available on your hardware:

| Backend | When it's used | What it does well |
|---------|---------------|------------------|
| **Taichi GPU** | Apple Silicon or CUDA GPU detected | Particle systems, animated gradients, globe rendering, SDF text. Full cinematic quality. |
| **PIL** | No GPU, or `--no-animated-background` | Static gradients, clean text rendering. Still looks good, just no animation. |
| **FFmpeg** | Fallback for minimal environments | Text overlay via drawtext filter. Basic but functional. |

The renderer selection is automatic. You don't need to configure anything: if you have a GPU, you get particles and animations. If you don't, you get clean static cards. Both look intentional.

## Content-backed backgrounds

By default, title screens use a frame from your actual footage as the background. The system extracts a frame at the 1/3 mark of the first clip, applies a heavy blur (40px) and darkens it (45%), then renders white text on top. This means every title screen looks like it belongs to the video it introduces, rather than using a generic gradient.

An optional slow-motion background effect uses Catmull-Rom interpolation with cubic ease-in timing to animate the blurred background during the title card. Falls back to a static blurred frame when disabled.

## Visual styles

All styles use dark cinematic palettes with white text. No pastel or bright backgrounds. Five named styles are available:

| Style | Palette | Character |
|-------|---------|-----------|
| `modern_warm` | Warm charcoal/stone | Bold, semibold Montserrat. Amber accents. |
| `elegant_minimal` | Deep navy/black | Clean, medium weight. Cyan accents. |
| `vintage_charm` | Warm charcoal | Nostalgic feel. Amber/gold accents. |
| `playful_bright` | Midnight slate | Energetic, semibold. Purple accents. |
| `soft_romantic` | Warm stone/zinc | Gentle fades. Warm amber accents. |

### Mood-based selection (default)

By default (`style_mode: auto`), the system picks the style based on the video's detected mood. Each mood maps to a color palette, font family, animation preset, and font weight:

| Mood | Palette | Font | Animation |
|------|---------|------|-----------|
| happy | warm_dark | Quicksand | fade_up |
| calm | deep_teal | Raleway | slow_fade |
| energetic | midnight | Outfit | smooth_slide |
| nostalgic | warm_dark | Josefin Sans | slow_fade |
| romantic | warm_dark | Josefin Sans | gentle_scale |
| playful | midnight | Quicksand | fade_up |
| peaceful | deep_teal | Raleway | slow_fade |
| exciting | cinematic_dark | Outfit | smooth_slide |

Four color palettes are available: `cinematic_dark` (deep navy), `warm_dark` (warm stone/charcoal), `deep_teal` (ocean blue/teal), `midnight` (slate). All use white or near-white text.

Set `style_mode: random` to pick a named style at random instead of using mood detection. Or pass `--style elegant_minimal` to the titles CLI to force a specific style.

## Map Animation

Trip memories start with an animated satellite fly-over from your home location to the destination. It's a Google Earth-style zoom that gives context before the clips start.

### What it does

The animation starts at city-level zoom on your home location, flies out and across to the destination(s), then settles at a zoom level that shows all the destination pins. Duration is configurable (default 5 seconds).

Each endpoint gets a pin (red circle with white outline) and a city label. The title text fades in over the satellite imagery, sitting in the lower third so it doesn't block the map.

### Zoom vs. pan selection

Two animation modes get picked automatically based on how far apart departure and destination are:

- **Van Wijk zoom**: for long distances. Zooms out to show the route, then zooms back in. The math is the d3 `interpolateZoom` algorithm: it picks the smoothest path through zoom-space rather than just linearly interpolating.
- **Linear pan**: for short hops. When the destination is close enough that the mid-transit zoom would stay above zoom level 10 (roughly city-block level), it just pans at fixed zoom instead of zooming out unnecessarily.

### Tile source

Satellite imagery comes from ArcGIS World Imagery tiles: no API key required. Tiles are cached in-memory during rendering to avoid redundant fetches across adjacent frames.

### Map styles

The static map renderer supports three styles, used for pin-and-label map frames (not the fly-over animation):

| Key | Source | Notes |
|-----|--------|-------|
| `satellite` | ArcGIS World Imagery | Default. Used for the fly-over. |
| `osm` | OpenStreetMap | Clean street map, good for city-level |
| `topo` | OpenTopoMap | Topographic, useful for hiking trips |

Configure with `map_style: osm` under `title_screens` if you want a different style for location cards.

## Trip Classification

The LLM looks at which locations repeat across days:

| Pattern | Classification | Map mode | Real example |
|---------|---------------|----------|-------------|
| Same spot every night, excursions during the day | `base_camp` | `excursions` | Val d'Aoste: Ville Sur Sarre as base, hikes to Cogne and La Thuile |
| 2-3 spots, each for multiple consecutive days | `multi_base` | `overnight_stops` | Cyprus: 5 nights in Nicosia, 5 nights in Geroskipou |
| Different town each day, big distances | `road_trip` | `overnight_stops` | Italy 2022: 14 days from Umbria through Marche to Alsace |
| Daily moves but short distances, progressive | `hiking_trail` | `overnight_stops` | Saxon Switzerland: Hohnstein to Bad Schandau to Konigstein |

## Configuration

Title screen settings live under `title_screens` in your config:

```yaml
title_screens:
  style_mode: auto              # auto (mood-based) or random
  animated_background: true     # GPU-accelerated backgrounds (auto-detected)
  title_duration: 3.5           # seconds per title card
  locale: auto                  # auto, en, or fr
  show_decorative_lines: false  # subtle line accents
  show_month_dividers: true     # month dividers in yearly memories
  use_first_name_only: true     # "Emma" instead of "Emma Dumont"
```

Map coordinates for trip memories come from the `trips` config section:

```yaml
trips:
  homebase_latitude: 48.8566    # Your home coordinates
  homebase_longitude: 2.3522
```

## Previewing before a full render

The `titles test` CLI command generates standalone title cards so you can dial in the look without running a full pipeline:

```bash
# Quick preview
immich-memories titles test --year 2025 --style elegant_minimal

# Birthday card
immich-memories titles test --birthday-age 3 --person "Emma" --year 2025

# French, portrait orientation
immich-memories titles test --year 2025 --locale fr --orientation portrait
```

See [CLI: titles](../cli/titles.md) for the full flag reference.
