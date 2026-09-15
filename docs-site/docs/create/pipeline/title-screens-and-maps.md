---
sidebar_position: 8
title: Title Screens & Maps
---

# Title Screens & Maps

An intro card, a divider at every month change, a satellite fly-over for a trip, location cards
between segments, an ending. This is the structure around the clips, and it is the difference
between a memory and an FFmpeg concat of your footage.

Which of them a memory gets depends on its type:

- **Intro card**: content-backed background (a blurred, darkened frame from your footage), white
  title text with an entrance animation, optional subtitle (person name, date range). 3.5 seconds.
- **Month dividers**: at every month change, for any single-year range spanning four or more months.
  The first month's divider is skipped: the intro card already said it. `month_divider_threshold`
  does not gate insertion; it sizes a budget cap, and when the cap binds it keeps the *first* N
  month changes chronologically. So a one-clip January can get a card while a dense November loses
  one.
- **Trip map animation**: a satellite fly-over from home to destination, replacing the generic intro
  for trip memories.
- **Location cards**: city name and map thumbnail between trip segments.
- **Ending**: a fade-to-white card. No text: every ending path renders with an empty title.

## Two rendering backends

| Backend | When it's used | What it does well |
|---------|---------------|------------------|
| **GPU kernels** | The kernel library imports and initialises: Metal, CUDA or Vulkan; it will also run on its own CPU backend and say so in the log | Bokeh particle systems over a content-backed card. SDF text exists in the tree but both production call sites pin it off. |
| **PIL** | The kernel library has no wheel for this platform, or fails to initialise | Static gradients, clean text rendering. Still looks good, just no animation. |

The choice is automatic and there is nothing to configure. `--no-animated-background` is a
different switch: it stays on whichever backend you have and turns off the gradient rotation,
colour pulse and vignette pulse. On the shipped default it changes nothing, because a
content-backed title zeroes all three anyway; it only bites if you have turned content backgrounds
off. It does not send you to PIL.

## Content-backed backgrounds

By default a title screen's background is the first second of the first clip, blurred and darkened,
with white text over it. So every card looks like it belongs to the video it introduces rather than
to a generic gradient.

How hard it is blurred and dimmed depends on the renderer. The GPU path blurs by a tenth of the frame
height and dims proportionally to how much blur it applied; PIL uses a fixed Gaussian and a flat
multiplier. Neither is a config key.

The GPU path animates that background in slow motion, interpolating between the decoded frames
with Catmull-Rom and a cubic ease-in. There is no switch for it: it falls back to a single blurred
frame when the clip cannot be decoded or yields fewer than five frames.

## Visual styles

All styles use dark cinematic palettes with white text. No pastel or bright backgrounds.

| Style | Palette | Character |
|-------|---------|-----------|
| `modern_warm` | Warm charcoal/stone | Bold, semibold Montserrat. Amber accents. |
| `elegant_minimal` | Deep navy/black | Clean, medium weight. Cyan accents. |
| `vintage_charm` | Warm charcoal | Nostalgic feel. Amber/gold accents. |
| `playful_bright` | Deep teal | Energetic, semibold. Teal accents. |
| `soft_romantic` | Dark amber-tinted | Gentle scale-in. Warm amber accents. |

On `style_mode: auto`, the default, the style comes from the memory's detected mood:

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

Four palettes exist: `cinematic_dark` (deep navy), `warm_dark` (warm stone/charcoal), `deep_teal`
(ocean blue/teal), `midnight` (slate). Set `style_mode: random` to pick a named style at random
instead, or pass `--style elegant_minimal` to the titles CLI to force one.

## The map fly-over

A trip memory opens on your home location at city-level zoom, flies out and across to the
destinations, and settles at a zoom that shows every pin. It runs for `title_duration`, 3.5 seconds
by default. Each endpoint gets a pin (red circle, white outline) and a city label; the title text
fades in over the imagery in the lower third, where it doesn't block the map.

Two animations, picked on the distance between departure and destination:

- **Van Wijk zoom**, for long distances. Zooms out to show the route, then back in. The maths is the
  d3 `interpolateZoom` algorithm: it picks the smoothest path through zoom-space rather than
  interpolating linearly. The further apart the two points, the further the camera pulls out
  mid-flight.
- **Linear pan**, for short hops. When the mid-transit zoom would stay at level 10 or above (metro
  scale), it pans at fixed zoom instead of zooming out for nothing.

Imagery is ArcGIS World Imagery, no API key, cached in memory during rendering so adjacent frames
don't refetch the same tiles.

The static map renderer also knows `osm` (OpenStreetMap, clean street map) and `topo`
(OpenTopoMap, useful for hiking trips), used for the pin-and-label frames rather than the fly-over.
Both are internal today: `map_style` is a parameter on the renderer functions in
`titles/map_renderer.py`, not a `title_screens` key, so a generate run always gets satellite.

## Trip classification

The model reads which locations repeat across days and names the pattern:

| Pattern | Classification | Example |
|---------|---------------|---------|
| Same spot every night, excursions during the day | `base_camp` | A week in a mountain village, day hikes into two neighbouring valleys |
| 2-3 spots, each for multiple consecutive days | `multi_base` | An island: 5 nights in the capital, 5 nights on the coast |
| Different town each day, big distances | `road_trip` | Two weeks driving through three regions, a new town every night |
| Daily moves but short distances, progressive | `hiking_trail` | A hut-to-hut trail: three villages a day's walk apart |

It returns a `map_mode` (`excursions`, `overnight_stops` or `title_only`) alongside the
classification. Nothing in the renderer reads it: today it is a badge in the UI and changes no
pixels. What the model is handed, and what else comes back with the classification, is on
[LLM titles and mood](./llm-content-analysis.md).

## Configuration

The `title_screens:` and `trips:` keys, with their defaults, are in the
[config reference](../../reference/config-reference.md#title-screens). The switches worth knowing
about: `enabled` turns the whole thing off, including the map fly-over and its tile requests, and
`style` pins one of the visual styles above instead of letting the mood pick.

## Previewing before a full render

`titles test` renders standalone cards, so you can dial in the look without running a pipeline:

```bash
# Quick preview
immich-memories titles test --year 2025 --style elegant_minimal

# Birthday card
immich-memories titles test --birthday-age 3 --person "Emma" --year 2025

# French, portrait orientation
immich-memories titles test --year 2025 --locale fr --orientation portrait
```

See [CLI: titles](../cli/titles.md) for the full flag reference.
