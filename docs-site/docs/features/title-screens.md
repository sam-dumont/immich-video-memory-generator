---
sidebar_position: 6
title: Title Screens & Visual Polish
---

# Title Screens & Visual Polish

Title screens are what makes the output look like something you'd actually show people. Without them it's just FFmpeg concat. With them it's a memory video that feels produced.

The system generates animated intros, month dividers, trip maps, globe fly-overs, and ending sequences. All from your actual data: dates, locations, person names.

## Two background modes

### Content-backed (default)

Extracts the first 0.5 seconds of your video, slows it to 3.5 seconds with cubic interpolation, applies heavy blur + darken, and overlays bold white Montserrat text. The blur lifts in the last second, then hard-cuts to the clip.

The ending mirrors this: last 0.5 seconds in reverse slow-mo, blur increases, fades to white.

<!-- TODO: add screenshot of content-backed intro -->
<!-- TODO: add screenshot of content-backed ending -->
<!-- TODO: add screenshot of deblur reveal moment -->

When source clips are HDR (HLG/bt2020), the entire pipeline stays in 16-bit rgb48le. No SDR conversion anywhere. White text at peak HDR brightness.

### Gradient (classic)

Dark cinematic gradient background with standard crossfade transitions. Four mood-based palettes:

| Palette | Colors | Used for |
|---------|--------|----------|
| `cinematic_dark` | Deep navy | Default, exciting moods |
| `warm_dark` | Warm charcoal | Happy, nostalgic, romantic |
| `deep_teal` | Deep teal/ocean | Calm, peaceful |
| `midnight` | Slate midnight | Energetic, playful |

<!-- TODO: add screenshot of gradient intro -->

## What gets generated

- **Intro card**: 3.5 seconds. Slow-mo content background with deblur reveal, or dark gradient.
- **Month dividers**: inserted when the month changes in yearly memories. The first clip doesn't get a divider (the intro already covers that).
- **Trip map animation**: satellite fly-over from home to destination using Van Wijk zoom. Replaces the generic intro for trip memories.
- **Globe rendering**: 3D globe for long-distance trips. GPU-accelerated via Taichi.
- **Location cards**: city name + map thumbnail between trip segments.
- **Ending sequence**: 4 seconds. Reverse slow-mo blur + fade to white.

## Configuration

```yaml
title_screens:
  style_mode: auto              # auto (mood-based) or random
  title_background: content_backed  # "content_backed" or "gradient"
  animated_background: true     # GPU-accelerated backgrounds (gradient mode only)
  title_duration: 3.5           # seconds per intro card
  ending_duration: 4.0          # seconds for ending screen
  locale: auto                  # auto, en, or fr
  show_month_dividers: true     # month dividers in yearly memories
  use_first_name_only: true     # "Emma" instead of "Emma Dumont"
```

### Privacy mode

Content-backed titles in privacy mode show the privacy-blurred content with additional title blur on top. The deblur reveal transitions from heavily blurred to privacy-blurred: content stays unidentifiable throughout.

## Rendering backends

| Backend | When it's used | What it does |
|---------|---------------|-------------|
| **Taichi GPU** | Apple Silicon or CUDA GPU detected | Animated blur, bokeh particles, SDF text |
| **PIL** | No GPU, or `--no-animated-background` | Static backgrounds, pixel-sharp text |

Selection is automatic. GPU gives you animations and particles. CPU gives you clean static cards. Both look intentional.

## Previewing

Generate standalone title cards without running the full pipeline:

```bash
immich-memories titles test --year 2025
immich-memories titles test --birthday-age 3 --person "Emma" --year 2025
immich-memories titles test --year 2025 --locale fr --orientation portrait
```

See [CLI: titles](../create/cli/titles.md) for all flags.
