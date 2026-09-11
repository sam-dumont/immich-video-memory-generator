---
sidebar_position: 6
title: Photo Support
---

# Photo Support

Include photos alongside videos in your memory compilations. Photos are converted to animated video clips using Ken Burns effects, face-aware panning, and blur backgrounds.

## How It Works

Photos compete in the same selection pool as videos and live photos. There's no separate "photo pipeline": everything goes through unified selection.

1. **Fetch**: every IMAGE asset in range is fetched from Immich, Live Photo stills included; a Live Photo's still is a photograph, and whether its burst is worth showing as motion is a rendering question asked later, about an asset that already won its place
2. **Read**: each photo gets its caption, context heads, detector facts and pixel facts prepared once; see [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md)
3. **Edit**: photos and videos are one pool; the editor weighs the period's stories and grants pictures by weight, and a still is held for the seconds it earns
4. **Render**: selected photos are animated as Ken Burns clips at assembly time

## Animation Effects

### Ken Burns (default)
Slow zoom + pan over the photo. The camera pans toward detected faces when face data is available from Immich. Pan direction is randomized per photo for variety.

### Blur Background
When a portrait photo is displayed in a landscape frame (or vice versa), the mismatched area is filled with a dynamically blurred version of the photo content. The photo stays centered at full size while the blur decorates around it.

### Face-Aware Pan
When Immich has detected faces in a photo, the Ken Burns camera automatically pans toward the largest face. The face position comes from Immich's ML face detection bounding boxes.

Every photo goes through the same renderer: a Ken Burns zoom of 5–12 % (seeded from the asset ID so re-runs are stable) that pans toward the face target, over a blurred background when the aspect ratios don't match. A split-screen renderer sits in the code base unwired, waiting on the split-mode feature; the collage and slide-in renderers that never got wired up were deleted.

## HEIC/HEIF Support

iPhone photos stored as HEIC are decoded via `pillow-heif` (pure Python, cross-platform). FFmpeg cannot properly decode HEIC files: it reads thumbnail tiles instead of the full-resolution image.

## HDR Support

### Apple HDR (iPhone 12+)
iPhone photos include an HDR gain map stored as an auxiliary image in the HEIF container. The gain map specifies how to boost highlights for HDR displays:

- Base image: 8-bit SDR with Display P3 color space
- Gain map: grayscale map indicating per-pixel brightness boost
- Headroom is extracted per-photo from EXIF MakerNote metadata (tag 0x0021)
- Formula: `HDR_linear = SDR_linear * 2^(gain * headroom)`
- Output: HEVC 10-bit PQ/BT.2020 (HDR10)

The headroom value varies per photo depending on scene brightness (e.g. 0.74 for low-light, 1.69 for direct sunlight). This ensures accurate HDR brightness matching the original HEIC.

:::tip Optional: exiftool fallback
If the EXIF MakerNote parsing fails, the system falls back to [exiftool](https://exiftool.org/) for headroom extraction. exiftool is not required: it's only used as a safety net. Install it via `brew install exiftool` (macOS) or `apt install libimage-exiftool-perl` (Debian/Ubuntu).
:::

### Ultra HDR (Android/Pixel)
Android Ultra HDR JPEGs (ISO 21496-1) embed a gain map as an MPF secondary image with `hdrgm` XMP metadata. The reconstruction formula supports per-channel gamma, display-adaptive weight, and configurable offsets.

## Configuration

```yaml
photos:
  enabled: true           # Include photos in memories
  duration: 4.0           # Seconds per photo clip
  burst_window_seconds: 300  # Photos this close and near-identical are one burst
  burst_hash_threshold: 8    # Hash bits two frames may differ by and still be one burst
```

Older configs may still contain `collage_duration`, `animation_mode`, `enable_collage`, `series_gap_seconds` or `zoom_factor`; those keys were removed in 0.41 and are ignored (the zoom amount is randomized per photo, and collages no longer exist).

## One photo per burst

A held shutter produces near-identical frames seconds apart. Before scoring, photos
within `burst_window_seconds` of each other whose thumbnails are within
`burst_hash_threshold` bits are treated as one burst, and only the best-scored frame
survives. On a real June library that removed **64 of 303 photos, 21% of the pool**,
in groups of up to five.

Both conditions are required. Time alone would collapse a busy minute at a party;
similarity alone would merge the same kitchen photographed a month apart. A photo with
no cached thumbnail is always kept: redundancy is measured, never assumed. Set
`burst_window_seconds: 0` to turn it off.

Because this runs before the LLM shortlist, every photo it removes is also an LLM call
saved.

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
