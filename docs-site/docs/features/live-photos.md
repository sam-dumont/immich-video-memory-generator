---
sidebar_position: 5
title: Live Photos
---

# Live Photos

Every time you take a photo on an iPhone, it also captures ~3 seconds of video. Most people don't realize they're sitting on thousands of candid video moments. immich-memories can pull these clips from Immich and use them in your memory videos.

## How it works

1. **Discovery**: queries Immich for IMAGE assets that have a linked `livePhotoVideoId`
2. **Person filtering**: when a person is selected, searches by person with no asset type filter, then filters client-side for live photos (see [Person filtering quirk](#person-filtering-quirk) below)
3. **Neighbor expansion**: untagged live photos taken within the merge window of a tagged one get pulled in automatically
4. **Clustering**: groups photos taken within a configurable time window (default 10 seconds) into bursts
5. **Burst merging**: if a cluster has 3+ photos (configurable), their video clips get stitched into one longer clip via FFmpeg
6. **Pipeline integration**: merged clips and standalone Live Photos enter the normal analysis pipeline alongside regular video clips

## Burst merging: the shutter-handoff algorithm

When you rapid-fire photos, each Live Photo's video overlaps with the next one. Blindly concatenating them would repeat footage, so the merger uses a shutter-handoff approach instead.

Each clip plays from its start until the moment the **next** photo was taken. The next clip picks up from that exact shutter press. Transitions happen at moments of interest: the points where you were actively pressing the shutter.

Example with 3 photos taken at t=0, t=0.5s, and t=2s (each clip is 3 seconds, covering 1.5s before to 1.5s after shutter):

| Clip | Plays from | Plays to | Duration |
|------|-----------|----------|----------|
| Photo 1 | -1.5s (start) | 0.5s (Photo 2's shutter) | 2.0s |
| Photo 2 | 0.5s (its shutter) | 2.0s (Photo 3's shutter) | 1.5s |
| Photo 3 | 2.0s (its shutter) | 3.5s (end) | 1.5s |

Result: 5.0 seconds of continuous, non-repeating footage from 3 photos.

When the gap between photos exceeds the clip duration (no overlap), each clip plays in full with no trimming.

## Person filtering quirk

Immich's search API has a blind spot with live photos: if you search for IMAGE assets filtered by a person, the results come back without the `livePhotoVideoId` field populated. The live photos are there, but they look like regular photos.

The workaround: search by person with NO asset type filter, then filter client-side for `is_live_photo`. This returns the full data including video component IDs.

There's a second problem: Immich's face detection doesn't tag every live photo in a burst. You take 5 rapid-fire photos of your kid, Immich tags them in 3 of them (face angle, motion blur, etc.). The other 2 are clearly from the same moment but would get dropped by a strict person filter.

So we do "neighbor expansion": after finding person-tagged live photos, we also fetch ALL live photos for the date range and include any untagged ones that fall within the merge window (default 10s) of a tagged one. The same window that's used for burst clustering, so the expansion matches exactly what becomes a single merged clip.

## Configuration

```yaml
analysis:
  include_live_photos: false              # Opt-in (default off)
  live_photo_merge_window_seconds: 10     # Max gap between photos to form a burst (1-60s)
  live_photo_min_burst_count: 3           # Min photos needed for burst merging (2-20)
```

In the UI wizard, there's a toggle in the Options section on Step 1. Via CLI:

```bash
immich-memories generate --include-live-photos --period "2024"
```

## Device support

Live Photos are stored in Immich with a `livePhotoVideoId` field linking the photo to its video component. Immich normalizes this across device types:

| Device | Format | Immich support | Tested |
|--------|--------|:-:|:-:|
| iPhone | HEVC Live Photo | Yes | Yes |
| Samsung | Motion Photo (MP4 embedded in JPEG) | Yes | No |
| Google Pixel | Motion Photo (MP4 embedded) | Yes | No |

I only use iOS devices, so Samsung and Pixel support is theoretical. It _should_ work since Immich normalizes everything to the same `livePhotoVideoId` field, but nobody's tested it yet. PRs from Android users welcome.

## Known limitations

Burst merging is functional but not fully polished yet. The main rough edge: **visible jumps between clips** in a burst. Each Live Photo's video has slightly different framing, exposure, and white balance (the phone adjusts between shots), so the handoff at shutter points can produce a visible discontinuity. Cross-fading between burst segments is on the roadmap.

The core value (turning rapid-fire photos into continuous clips) works well despite these rough edges.

## When to enable

Live Photos work best when you have a library with lots of photos and relatively few videos. Burst merging is particularly effective for events where you took rapid-fire photos (birthdays, travel, kids playing): those bursts become 10-15 second continuous clips that capture the moment way better than any individual photo.

If your library is mostly video already, Live Photos will add noise. Start with `include_live_photos: false` (the default) and experiment.
