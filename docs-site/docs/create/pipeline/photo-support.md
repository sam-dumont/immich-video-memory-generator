---
sidebar_position: 6
title: Photo Support
---

# Photo Support

Include photos alongside videos in your memory compilations. Photos are converted to animated video clips using Ken Burns effects, face-aware panning, and blur backgrounds.

## How It Works

Photos, videos and Live Photos share one selection pool.

1. **Fetch**: photographs matching the source and people filters are fetched from Immich, including Live Photo stills
2. **Read**: each eligible photo gets the facts required by the configured preparation tier; matching cached facts are reused. See [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md)
3. **Edit**: photos and videos are one pool; the editor weighs the period's stories and grants pictures by weight, and a still is held for the seconds it earns
4. **Render**: selected photos are animated as Ken Burns clips at assembly time

## Animation Effects

### Ken Burns (default)
Slow zoom + pan over the photo. The camera pans toward detected faces when face data is available from Immich. Pan direction is randomized per photo for variety.

### Blur Background
When a portrait photo is displayed in a landscape frame (or vice versa), the mismatched area is filled with a dynamically blurred version of the photo content. The photo stays centered at full size while the blur decorates around it.

### Face-Aware Pan
When Immich has detected faces in a photo, the Ken Burns camera automatically pans toward the largest face. The face position comes from Immich's ML face detection bounding boxes.

Photos use a Ken Burns zoom of 5–12%, seeded from the asset ID so reruns use the same movement. With no face target, the pan ends at the centre.

## HEIC/HEIF Support

iPhone photos stored as HEIC are decoded through `pillow-heif`, which wraps libheif. The app uses this decoder to read the full image and its gain map.

## HDR Support

Apple HEIC and Android Ultra HDR JPEG gain maps are used when present. Photo intermediates
use 10-bit PQ/BT.2020 when FFmpeg has `zscale`; final output follows the selected codec and
HDR mode. See [HDR](./hdr.md) for the supported combinations and fallbacks.

:::tip Optional: exiftool fallback
If the EXIF MakerNote parsing fails, the system falls back to [exiftool](https://exiftool.org/) for headroom extraction. exiftool is not required: it's only used as a safety net. Install it via `brew install exiftool` (macOS) or `apt install libimage-exiftool-perl` (Debian/Ubuntu).
:::

## Configuration

```yaml
photos:
  enabled: true           # Include photos in memories
  duration: 4.0           # Seconds per photo clip
  burst_window_seconds: 300  # Photos this close and near-identical are one burst
  burst_hash_threshold: 8    # Hash bits two frames may differ by and still be one burst
```

Remove `photos.max_ratio`, `read_moments`, `moment_gap_seconds` and `moment_hash_threshold`
from older configs. The loader ignores these retired settings and logs a warning naming them.

## One photo per burst

A held shutter produces near-identical frames seconds apart. Before the editor chooses
anything, photos within `burst_window_seconds` of each other whose thumbnails are within
`burst_hash_threshold` bits are treated as one burst. A favourite wins; otherwise the
measured quality decides which frame survives.

Both conditions are required. Time alone would collapse a busy minute at a party;
similarity alone would merge the same kitchen photographed a month apart. A photo with
no cached thumbnail is kept by this duplicate check. Setting `burst_window_seconds: 0`
limits comparison to identical capture timestamps; it does not disable the check completely.

## CLI Flags

```bash
# Include photos in generation
immich-memories generate --include-photos --year 2024

# Leave photos out even when photos.enabled is true in config
immich-memories generate --no-photos --year 2024

# Same for Live Photos
immich-memories generate --no-live-photos --year 2024

# Override photo duration
immich-memories generate --include-photos --photo-duration 5.0

# Photos are also enabled via config:
# photos.enabled: true in config.yaml
```
