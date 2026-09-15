---
sidebar_position: 6
title: Photo Support
---

# Photo Support

Photos compete in the same pool as videos. There is no separate photo pipeline: a still that wins a
slot is animated into a clip at assembly time, and it holds the screen for the seconds the editor
granted it.

1. **Fetch**: every IMAGE asset in range comes back from Immich, Live Photo stills included. A Live
   Photo's still is a photograph, and whether its burst is worth showing as motion is a rendering
   question asked later, about an asset that already won its place
2. **Read**: caption, context heads, detector facts and pixel facts are prepared once; see
   [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md)
3. **Edit**: photos and videos are one pool, weighed by the period's stories
4. **Render**: Ken Burns, at assembly time

## One renderer, every photo

A zoom of 5 to 12 %, seeded from the asset ID so re-runs are stable, panning toward the largest face
Immich found ([Face-aware framing](./face-aware-cropping.md)), over a blurred copy of the photo
itself when the aspect ratios don't match. Pan direction is randomised per photo.

A split-screen renderer sits in the code base unwired, waiting on the split-mode feature. The
collage and slide-in renderers that never got wired up were deleted.

## HEIC and HDR

HEIC is decoded with `pillow-heif`: FFmpeg reads only the thumbnail tiles of a HEIC, so it is not
used here. An Apple or Android gain map is reconstructed rather than flattened and ships as 10-bit
PQ. The tags, the formula and what it was checked against are on [HDR, end to end](./hdr.md).

## One photo per burst

A held shutter collapses to a single frame before the editor chooses anything.
[Twins and near-duplicates](./duplicate-detection.md) has both conditions and what it removed on a
real library. `photos.burst_window_seconds: 0` turns it off.

## Configuration

The `photos:` keys and their defaults are in the
[config reference](../../reference/config-reference.md#photos). `--include-photos` / `--no-photos`
and `--photo-duration` are in the [CLI reference](../../reference/cli-reference.md#generate); a flag
wins over `photos.enabled` for one run and changes nothing on disk.

Older configs may still carry `collage_duration`, `animation_mode`, `enable_collage`,
`series_gap_seconds` or `zoom_factor`. Those five are ignored in silence: the zoom amount is
randomised per photo now, and collages no longer exist. Four more went with the clip scorer and are
dropped by name instead: `max_ratio`, `read_moments`, `moment_gap_seconds` and
`moment_hash_threshold`. A config file that still names one starts normally and logs one warning
listing every one it dropped, so an old file cannot keep a setting that quietly does nothing.
