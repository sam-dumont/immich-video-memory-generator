---
sidebar_position: 8
title: Title Screens & Maps
---

# Title Screens & Maps

This is where the output stops looking like "FFmpeg concat" and starts looking like something you'd actually want to show people.

Title screens are the structural connective tissue: animated intro cards, month dividers, satellite map fly-overs, and ending sequences. Same kind of polish as Relive, but running on your own hardware with your own data.

## What gets generated

Depending on the memory type, title screens include some or all of:

- **Intro card**: content-backed background (blurred + darkened frame from your footage), white title text with entrance animation, optional subtitle (person name, date range). 3.5 seconds.
- **Month dividers**: a divider card at every month change, for any single-year range spanning four or more months. The first month's divider is skipped: the intro card already said it. `month_divider_threshold` does not gate insertion; it sizes a budget cap, and when the cap binds it keeps the *first* N month changes chronologically. So a one-clip January can get a card while a dense November loses one.
- **Trip map animation**: satellite fly-over from home to destination using Van Wijk zoom. Replaces the generic intro for trip memories. The further apart the two points, the further the camera pulls out mid-flight.
- **Location cards**: city name + map thumbnail between trip segments.
- **Ending sequence**: a fade-to-white card. No text: every ending path renders with an empty title.

## Two rendering backends

| Backend | When it's used | What it does well |
|---------|---------------|------------------|
| **Taichi** | Taichi imports and initialises: Metal, CUDA or Vulkan; it will also run on its own CPU backend and say so in the log | Bokeh particle systems over a content-backed card. SDF text exists in the tree but both production call sites pin it off. |
| **PIL** | Taichi is not installed, or fails to initialise | Static gradients, clean text rendering. Still looks good, just no animation. |

The choice is automatic and there is nothing to configure. `--no-animated-background` is a
different switch: it stays on whichever backend you have and turns off the gradient rotation,
colour pulse and vignette pulse. On the shipped default it changes nothing, because a
content-backed title zeroes all three anyway; it only bites if you have turned content backgrounds
off. It does not send you to PIL.

There is a third renderer in the tree, `titles/renderer_ffmpeg.py`, that draws titles with
`drawtext`. Nothing in the product imports it: only tests do. Treat it as unwired.

## Content-backed backgrounds

By default, title screens use a frame from your actual footage as the background: the first second
of the first clip, blurred and darkened, with white text over it. So every title screen looks like
it belongs to the video it introduces rather than to a generic gradient.

How hard it is blurred and dimmed depends on the renderer. Taichi blurs by a tenth of the frame
height and dims proportionally to how much blur it applied; PIL uses a fixed Gaussian and a flat
multiplier. Neither is a config key.

The Taichi path animates that background in slow motion, interpolating between the decoded frames
with Catmull-Rom and a cubic ease-in. There is no switch for it: it falls back to a single blurred
frame when the clip cannot be decoded or yields fewer than five frames.

## Visual styles

All styles use dark cinematic palettes with white text. No pastel or bright backgrounds. Five named styles are available:

| Style | Palette | Character |
|-------|---------|-----------|
| `modern_warm` | Warm charcoal/stone | Bold, semibold Montserrat. Amber accents. |
| `elegant_minimal` | Deep navy/black | Clean, medium weight. Cyan accents. |
| `vintage_charm` | Warm charcoal | Nostalgic feel. Amber/gold accents. |
| `playful_bright` | Deep teal | Energetic, semibold. Teal accents. |
| `soft_romantic` | Dark amber-tinted | Gentle scale-in. Warm amber accents. |

### Mood-based selection (default)

By default (`style_mode: auto`), the system picks the style based on the video's detected mood. Each mood maps to a color palette, font family, animation preset, and font weight:

| Mood | Palette | Animation |
|------|---------|-----------|
| happy | warm_dark | fade_up |
| calm | deep_teal | slow_fade |
| energetic | midnight | smooth_slide |
| nostalgic | warm_dark | slow_fade |
| romantic | warm_dark | gentle_scale |
| playful | midnight | fade_up |
| peaceful | deep_teal | slow_fade |
| exciting | cinematic_dark | smooth_slide |

There is no font column because there is no font choice: every title is Montserrat. The style
definitions carry a `preferred_fonts` list that nothing reads.

Four color palettes are available: `cinematic_dark` (deep navy), `warm_dark` (warm stone/charcoal), `deep_teal` (ocean blue/teal), `midnight` (slate). All use white or near-white text.

Set `style_mode: random` to pick a named style at random instead of using mood detection. Or pass `--style elegant_minimal` to the titles CLI to force a specific style.

## Map Animation

Trip memories start with an animated satellite fly-over from your home location to the destination. It's a Google Earth-style zoom that gives context before the clips start.

### What it does

The animation starts at city-level zoom on your home location, flies out and across to the destination(s), then settles at a zoom level that shows all the destination pins. It runs for `title_duration`, 3.5 seconds by default.

Each endpoint gets a pin (red circle with white outline) and a city label. The title text fades in over the satellite imagery, sitting in the lower third so it doesn't block the map.

### Zoom vs. pan selection

Two animation modes get picked automatically based on how far apart departure and destination are:

- **Van Wijk zoom**: for long distances. Zooms out to show the route, then zooms back in. The math is the d3 `interpolateZoom` algorithm: it picks the smoothest path through zoom-space rather than just linearly interpolating.
- **Linear pan**: for short hops. When the destination is close enough that the mid-transit zoom would stay above zoom level 10 (metro scale), it just pans at fixed zoom instead of zooming out unnecessarily.

### Tile source

Satellite imagery comes from ArcGIS World Imagery tiles: no API key required. Tiles are cached in-memory during rendering to avoid redundant fetches across adjacent frames.

### Map styles

The static map renderer supports three styles, used for pin-and-label map frames (not the fly-over animation):

| Key | Source | Notes |
|-----|--------|-------|
| `satellite` | ArcGIS World Imagery | Default. Used for the fly-over. |
| `osm` | OpenStreetMap | Clean street map, good for city-level |
| `topo` | OpenTopoMap | Topographic, useful for hiking trips |

`osm` and `topo` are internal options today. `map_style` is a parameter on the renderer functions in `titles/map_renderer.py`, not a `title_screens` key, so a generate run always gets satellite.

## Trip Classification

The LLM looks at which locations repeat across days:

| Pattern | Classification | Example |
|---------|---------------|---------|
| Same spot every night, excursions during the day | `base_camp` | A week in a mountain village, day hikes into two neighbouring valleys |
| 2-3 spots, each for multiple consecutive days | `multi_base` | An island: 5 nights in the capital, 5 nights on the coast |
| Different town each day, big distances | `road_trip` | Two weeks driving through three regions, a new town every night |
| Daily moves but short distances, progressive | `hiking_trail` | A hut-to-hut trail: three villages a day's walk apart |

The model also returns a `map_mode` (`excursions`, `overnight_stops` or `title_only`) alongside
the classification. Nothing in the renderer reads it: today it is shown as a badge in the UI and
changes no pixels.

## Configuration

Title screen settings live under `title_screens` in your config:

```yaml
title_screens:
  style_mode: auto              # auto (mood-based) or random
  animated_background: true     # gradient shift, colour pulse, vignette; off on content-backed cards
  title_duration: 3.5           # seconds per title card
  locale: auto                  # auto, en, or fr
  show_decorative_lines: false  # inert: every style and every card pins line accents off
  show_month_dividers: true     # month dividers in yearly memories
  use_first_name_only: true     # "Riley" instead of "Riley Martin"
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
