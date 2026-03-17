---
sidebar_position: 5
title: Live Photos
---

# Live Photos

Every iPhone photo secretly records ~3 seconds of video. Most people have thousands of these clips sitting in their library without knowing it. immich-memories can pull them from Immich and use them in your memory videos.

## Demo: What burst merging looks like

Here's what happens when you rapid-fire 3 photos of an Italian hilltop. Each Live Photo is ~3 seconds. They overlap. The merger stitches them into one continuous clip:

**Individual source clips (3 separate Live Photos):**

<div style={{display: 'flex', gap: '8px', flexWrap: 'wrap'}}>
  <video width="240" controls muted><source src="/demos/live-photos/italian_hilltop/source_1.mp4" type="video/mp4" /></video>
  <video width="240" controls muted><source src="/demos/live-photos/italian_hilltop/source_2.mp4" type="video/mp4" /></video>
  <video width="240" controls muted><source src="/demos/live-photos/italian_hilltop/source_3.mp4" type="video/mp4" /></video>
</div>

**Merged result (4.5 seconds of continuous footage):**

<video width="720" controls><source src="/demos/live-photos/italian_hilltop/merged.mp4" type="video/mp4" /></video>

---

**Bike race — 6 Live Photos merged into 8.4 seconds:**

<video width="720" controls><source src="/demos/live-photos/bike_race/merged.mp4" type="video/mp4" /></video>

## How it works

1. **Discovery**: queries Immich for IMAGE assets with a linked `livePhotoVideoId`
2. **Person filtering**: searches by person with no asset type filter, then filters client-side for live photos (Immich quirk — see [below](#person-filtering-quirk))
3. **Neighbor expansion**: untagged live photos taken within the merge window of a tagged one get pulled in automatically
4. **Clustering**: groups photos taken within a configurable time window (default 3s) into bursts
5. **Spectrogram alignment**: cross-correlates audio between overlapping clips to find the exact temporal offset (sample-accurate, ~10ms per pair)
6. **Burst merging**: stitches clips with shutter-centered cuts, exposure normalization, and 30ms audio fade at boundaries
7. **Prioritization**: if there's enough regular video, live photos are available but not pre-selected

## Video prioritization

Real video clips are almost always better than live photo clips: longer, better stabilized, intentionally filmed. So when you enable live photos, immich-memories checks whether your regular videos already cover the target duration. If they do, live photos show up in the review grid (with a purple "Live" badge) but unchecked by default. You can manually add any that look good.

If there aren't enough regular videos to fill the target, live photos get selected automatically to make up the difference.

## Burst merging: spectrogram-aligned shutter-centered cuts

When you rapid-fire photos, each Live Photo's video overlaps with the next. The merger uses **audio spectrogram fingerprinting** to find the exact overlap, then cuts at the midpoint between consecutive shutter presses.

### Why audio alignment?

Timestamps alone aren't precise enough — each clip's video doesn't start at exactly `shutter_time - 1.5s`. The actual start varies by up to 200ms. On rapid bursts, that's enough to cause audible clicks and gaps.

The spectrogram (Short-Time Fourier Transform) creates a unique frequency fingerprint at every 5ms window. Even with repetitive beat-heavy music, the exact mix of frequencies is unique at each moment. Cross-correlating these fingerprints between clips gives sample-accurate alignment with 0.95+ confidence.

### The algorithm

1. Extract 48kHz mono audio from each clip
2. Compute STFT spectrogram (1024-sample window, 256 hop)
3. For each consecutive pair: correlate first 100ms of clip B against clip A to find where B's audio starts in A's timeline
4. Compute shutter-centered handoff points (midpoint between consecutive shutters)
5. Gap-aware: if a handoff falls before the next clip starts, extend the current clip to cover the hole
6. Build FFmpeg filter: trim each clip at its handoff points, normalize exposure, 30ms audio fade at boundaries, concatenate

### Example

3 photos at t=0, t=0.5s, t=2s (each clip ~3s):

| Clip | Plays from | Plays to | Duration |
|------|-----------|----------|----------|
| Photo 1 | start | midpoint(0, 0.5) = 0.25s | ~1.75s |
| Photo 2 | shutter-centered start | midpoint(0.5, 2.0) = 1.25s | ~1.5s |
| Photo 3 | shutter-centered start | end | ~1.5s |

Non-overlapping clips (gap > clip duration) are NOT merged — they stay as separate clips.

### Works for any phone

The algorithm uses audio fingerprinting, not Apple metadata. It works for iPhone, Samsung, Google Pixel, or any camera that records audio with video. The only requirement: overlapping clips with shared ambient audio.

## Person filtering quirk

Immich's search API has a blind spot: searching for IMAGE assets filtered by a person returns results without the `livePhotoVideoId` field populated. The live photos are there, but they look like regular photos.

The workaround: search by person with NO asset type filter, then filter client-side for `is_live_photo`. This returns the full data including video component IDs.

Second problem: Immich's face detection doesn't tag every live photo in a burst. You take 5 rapid-fire photos of your kid, Immich tags 3 of them. The other 2 are clearly from the same moment but would get dropped by a strict person filter.

The fix: "neighbor expansion". After finding person-tagged live photos, we fetch ALL live photos for the date range and include any untagged ones within the merge window of a tagged one.

## Configuration

```yaml
analysis:
  include_live_photos: false              # Opt-in (default off)
  live_photo_merge_window_seconds: 3      # Max gap between photos to form a burst
  live_photo_min_burst_count: 2           # Min photos needed for burst merging
```

In the UI wizard, there's a toggle in the Options section on Step 1. Via CLI:

```bash
immich-memories generate --include-live-photos --period "2024"
```

## Device support

Immich normalizes Live Photos across device types using the `livePhotoVideoId` field:

| Device | Format | Immich support | Tested |
|--------|--------|:-:|:-:|
| iPhone | HEVC Live Photo | Yes | Yes (iOS 15-18) |
| Samsung | Motion Photo (MP4 embedded in JPEG) | Yes | No |
| Google Pixel | Motion Photo (MP4 embedded) | Yes | No |

I only have iOS devices, so Samsung and Pixel support is theoretical. It _should_ work since Immich normalizes everything to the same field, and the audio alignment algorithm is phone-agnostic. PRs from Android users welcome.

## When to enable

Live Photos are most useful when your library has lots of photos and relatively few videos. Burst merging is particularly effective for events where you took rapid-fire photos (birthdays, travel, kids playing): those bursts become 5-15 second continuous clips that capture the moment better than any individual photo.

If your library already has plenty of video, live photos won't add much. Start with `include_live_photos: false` (the default) and experiment.
