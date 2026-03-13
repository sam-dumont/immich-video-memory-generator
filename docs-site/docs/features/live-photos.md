---
sidebar_position: 5
title: Live Photos
---

# Live Photos

Every iPhone photo secretly records ~3 seconds of video. Most people have thousands of these clips sitting in their library without knowing it. immich-memories can pull them from Immich and use them in your memory videos.

## How it works

1. **Discovery**: queries Immich for IMAGE assets with a linked `livePhotoVideoId`
2. **Person filtering**: searches by person with no asset type filter, then filters client-side for live photos (Immich has a quirk here: see [below](#person-filtering-quirk))
3. **Neighbor expansion**: untagged live photos taken within the merge window of a tagged one get pulled in automatically
4. **Clustering**: groups photos taken within a configurable time window (default 10s) into bursts
5. **Burst merging**: clusters with 3+ photos get stitched into one longer clip via FFmpeg
6. **Prioritization**: if there's enough regular video to fill the target duration, live photos are available but not pre-selected. They fill gaps, not crowd out real video.

## Video prioritization

Real video clips are almost always better than live photo clips: longer, better stabilized, intentionally filmed. So when you enable live photos, immich-memories checks whether your regular videos already cover the target duration. If they do, live photos show up in the review grid (with a purple "Live" badge) but unchecked by default. You can manually add any that look good.

If there aren't enough regular videos to fill the target, live photos get selected automatically to make up the difference.

## Burst merging: the shutter-handoff algorithm

When you rapid-fire photos, each Live Photo's video overlaps with the next. Concatenating them blindly would repeat footage, so the merger uses a shutter-handoff approach.

Each clip plays from its start until the moment the next photo was taken. The next clip picks up from that exact shutter press. Transitions happen at the points where you were actively pressing the shutter button.

Example with 3 photos taken at t=0, t=0.5s, and t=2s (each clip is 3s, covering 1.5s before to 1.5s after shutter):

| Clip | Plays from | Plays to | Duration |
|------|-----------|----------|----------|
| Photo 1 | -1.5s (start) | 0.5s (Photo 2's shutter) | 2.0s |
| Photo 2 | 0.5s (its shutter) | 2.0s (Photo 3's shutter) | 1.5s |
| Photo 3 | 2.0s (its shutter) | 3.5s (end) | 1.5s |

Result: 5.0 seconds of continuous, non-repeating footage from 3 photos.

When the gap between photos exceeds the clip duration (no overlap), each clip plays in full.

## Person filtering quirk

Immich's search API has a blind spot: searching for IMAGE assets filtered by a person returns results without the `livePhotoVideoId` field populated. The live photos are there, but they look like regular photos.

The workaround: search by person with NO asset type filter, then filter client-side for `is_live_photo`. This returns the full data including video component IDs.

Second problem: Immich's face detection doesn't tag every live photo in a burst. You take 5 rapid-fire photos of your kid, Immich tags 3 of them (face angle, motion blur, etc.). The other 2 are clearly from the same moment but would get dropped by a strict person filter.

The fix: "neighbor expansion". After finding person-tagged live photos, we fetch ALL live photos for the date range and include any untagged ones within the merge window (default 10s) of a tagged one. Same window as burst clustering, so the expansion matches exactly what becomes a single merged clip.

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

Immich normalizes Live Photos across device types using the `livePhotoVideoId` field:

| Device | Format | Immich support | Tested |
|--------|--------|:-:|:-:|
| iPhone | HEVC Live Photo | Yes | Yes |
| Samsung | Motion Photo (MP4 embedded in JPEG) | Yes | No |
| Google Pixel | Motion Photo (MP4 embedded) | Yes | No |

I only have iOS devices, so Samsung and Pixel support is theoretical. It _should_ work since Immich normalizes everything to the same field, but nobody's tested it yet. PRs from Android users welcome.

## Known limitations

Burst merging works but has rough edges. The main one: visible jumps between clips in a burst. Each Live Photo's video has slightly different framing, exposure, and white balance (the phone adjusts between shots), so the handoff at shutter points can produce a visible discontinuity. Cross-fading between burst segments is on the roadmap.

## When to enable

Live Photos are most useful when your library has lots of photos and relatively few videos. Burst merging is particularly effective for events where you took rapid-fire photos (birthdays, travel, kids playing): those bursts become 10-15 second continuous clips that capture the moment better than any individual photo.

If your library already has plenty of video, live photos won't add much. Start with `include_live_photos: false` (the default) and experiment.
