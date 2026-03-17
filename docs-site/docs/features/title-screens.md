---
sidebar_position: 6
title: Title Screens & Visual Polish
---

# Title Screens & Visual Polish

This is where the output stops looking like "FFmpeg concat" and starts looking like something you'd actually want to show people.

Title screens are the cinematic glue: animated intro cards, month dividers, trip maps, globe fly-overs, and ending sequences. They're what separates a clip dump from a memory video that feels produced. Think of it as the Relive-style polish, but running on your own hardware with your own data.

## Why this system exists

Every video compilation tool can stitch clips together. The hard part is making the result feel intentional. Google Photos adds minimal text. Apple uses canned templates. Relive renders a 3D globe animation. We went further: a full rendering pipeline with animated backgrounds, particle systems, text animations, and map integrations. All generated on-the-fly from your actual data (dates, locations, person names).

The goal: when you share a "2025 Family Highlights" video, it should look like you spent an afternoon in Final Cut. Not like a script ran overnight.

## What gets generated

Depending on the memory type, title screens include some or all of:

- **Intro card**: animated gradient background, stylized title text with entrance animation, optional subtitle (person name, date range). 3.5 seconds.
- **Month dividers**: for yearly memories, each month section gets a divider card. Keeps the viewer oriented in a 10-minute video.
- **Trip map animation**: satellite fly-over from home to destination using Van Wijk zoom (see [Map Animation](./map-animation.md)). Replaces the generic intro for trip memories.
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

## Visual styles

Five built-in styles, each with its own color palette, font pairing, and animation character:

| Style | Vibe |
|-------|------|
| `modern_warm` | Warm amber gradients, clean sans-serif |
| `elegant_minimal` | Dark backgrounds, thin serif, subtle particle drift |
| `vintage_charm` | Muted earth tones, slightly textured |
| `playful_bright` | Saturated colors, bouncy text animations |
| `soft_romantic` | Pastel gradients, gentle fades |

Set a style in config or pass `--style` to the titles CLI. Default is `random` (picks one per generation).

## Configuration

Title screen settings live under `title_screens` in your config:

```yaml
title_screens:
  style: modern_warm          # or elegant_minimal, vintage_charm, etc.
  animated_background: true   # GPU-accelerated backgrounds (auto-detected)
  duration: 3.5               # seconds per title card
  locale: en                  # en or fr for month names
```

For trip memories, additional map settings apply. See [Map Animation](./map-animation.md) and [Trip Memories](./trip-memories.md).

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
