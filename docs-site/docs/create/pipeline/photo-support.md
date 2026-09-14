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
- Headroom is extracted per-photo from two EXIF MakerNote tags read together, `0x0021` and `0x0030`
- Formula: `HDR_linear = SDR_linear * (1 + (headroom - 1) * gain_linear)`, Apple's linear
  interpolation. Reading only `0x0021` gave 2.01x where the true answer was 5.955x
- Output: HEVC 10-bit PQ/BT.2020 (HDR10)

Headroom is a linear ratio of at least 1.0 and varies per photo with scene brightness. The implementation was validated against CoreImage's own `kCIImageExpandToHDR` on 11 photographs across headrooms from 3.50 to 6.91: median error 1 to 2 % on most of them, 10 % on the worst that converged.

:::tip Optional: exiftool fallback
If the EXIF MakerNote parsing fails, the system falls back to [exiftool](https://exiftool.org/) for headroom extraction. exiftool is not required: it's only used as a safety net. Install it via `brew install exiftool` (macOS) or `apt install libimage-exiftool-perl` (Debian/Ubuntu).
:::

### Ultra HDR (Android/Pixel)
Android Ultra HDR JPEGs (ISO 21496-1) embed a gain map as an MPF secondary image with `hdrgm` XMP metadata. The reconstruction formula supports per-channel gamma, display-adaptive weight, and configurable offsets.

## Configuration

The `photos:` keys and their defaults are in the
[config reference](../../reference/config-reference.md#photos). Two of them decide what counts as a
burst: `burst_window_seconds` (300) is how close in time two frames must be, and
`burst_hash_threshold` (8) is how many hash bits they may differ by.

Older configs may still carry `collage_duration`, `animation_mode`, `enable_collage`,
`series_gap_seconds` or `zoom_factor`. Those five are ignored in silence: the zoom amount is
randomised per photo now, and collages no longer exist.

Four other `photos.*` keys are dropped by name rather than in silence: `max_ratio`, `read_moments`,
`moment_gap_seconds` and `moment_hash_threshold` went with the clip scorer. A config file that
still names one starts normally and logs one warning listing every one it dropped, so an old file
cannot keep a setting that quietly does nothing. Delete them to silence it.

## One photo per burst

A held shutter produces near-identical frames seconds apart. Before the editor chooses
anything, photos within `burst_window_seconds` of each other whose thumbnails are within
`burst_hash_threshold` bits are treated as one burst, and only the sharpest, best-exposed
frame survives. On a real June library that removed **64 of 303 photos, 21% of the pool**,
in groups of up to five.

Both conditions are required. Time alone would collapse a busy minute at a party;
similarity alone would merge the same kitchen photographed a month apart. A photo with
no cached thumbnail is always kept: redundancy is measured, never assumed. Set
`burst_window_seconds: 0` to turn it off.

## CLI Flags

`--include-photos` / `--no-photos` and `--photo-duration` are in the
[CLI reference](../../reference/cli-reference.md#generate) with their defaults. A flag wins over
`photos.enabled` for one run and changes nothing on disk.
