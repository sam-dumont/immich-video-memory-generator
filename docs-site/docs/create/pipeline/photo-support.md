---
sidebar_position: 6
title: Photo Support
---

# Photo Support

Include photos alongside videos in your memory compilations. Photos are converted to animated video clips using Ken Burns effects, face-aware panning, and blur backgrounds.

## How It Works

Photos compete in the same selection pool as videos and live photos. There's no separate "photo pipeline" — everything goes through unified selection.

1. **Fetch**: Photos (IMAGE assets, excluding live photos) are fetched from Immich
2. **Score**: Metadata scoring (favorites, faces, camera) + optional LLM visual analysis on thumbnails
3. **Merge**: Scored photos are converted to clip candidates and merged with analyzed video clips
4. **Select**: Unified Phase 4 picks from the combined pool — temporal dedup, duration scaling, and coverage guarantees apply to photos AND videos equally
5. **Render**: Selected photos are animated as Ken Burns clips at assembly time
6. **Interleave**: No more than 2 consecutive clips of the same type (photo or video)

Photos are capped at 50% of the final video when videos are plentiful. When videos are scarce (< 30% of selected clips), photos fill the budget freely.

## Animation Effects

### Ken Burns (default)
Slow zoom + pan over the photo. The camera pans toward detected faces when face data is available from Immich. Pan direction is randomized per photo for variety.

### Blur Background
When a portrait photo is displayed in a landscape frame (or vice versa), the mismatched area is filled with a dynamically blurred version of the photo content. The photo stays centered at full size while the blur decorates around it.

### Face-Aware Pan
When Immich has detected faces in a photo, the Ken Burns camera automatically pans toward the largest face. The face position comes from Immich's ML face detection bounding boxes.

### Slide-In
Photos slide into the frame with a smooth cubic ease-out animation, settling into the blur-background layout.

### Collage
2-4 photos displayed side by side (landscape) or stacked (portrait) with slide-in animation. Gaps between photos are filled with a blurred average of all photos.

### Split Screen
Apple Photos style grid: 2 photos side by side, 3 photos in a 2/3 + 1/3 layout, or 4 photos in a 2x2 grid.

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
  max_ratio: 0.50         # Max 50% of clips can be photos
  duration: 4.0           # Seconds per photo clip
  collage_duration: 6.0   # Seconds per collage clip
  animation_mode: auto    # auto | ken_burns | face_zoom | blur_bg
  enable_collage: true    # Group series as collages
  series_gap_seconds: 60  # Max gap to group as series
  zoom_factor: 1.15       # Ken Burns zoom amount (15%)
  score_penalty: 0.2      # Photos score 80% of equivalent videos
```

## CLI Flags

```bash
# Include photos in generation
immich-memories generate --include-photos --year 2024

# Override photo duration
immich-memories generate --include-photos --photo-duration 5.0

# Photos are also enabled via config:
# photos.enabled: true in config.yaml
```
