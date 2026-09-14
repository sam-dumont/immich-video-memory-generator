---
sidebar_position: 5
title: Live Photos
---

import Video from '@site/src/components/Video';

# Live Photos

Every iPhone photo secretly records ~3 seconds of video. Most people have thousands of these clips sitting in their library without knowing it. immich-memories can pull them from Immich and use them in your memory videos.

## Demo: What burst merging looks like

Here's what happens when you rapid-fire 3 photos of an Italian hilltop. Each Live Photo is ~3 seconds. They overlap. The merger stitches them into one continuous clip:

**Individual source clips (3 separate Live Photos):**

<div style={{display: 'flex', gap: '8px', flexWrap: 'wrap'}}>
  <Video src="/demos/live-photos/italian_hilltop/source_1.mp4" width={240} controls muted />
  <Video src="/demos/live-photos/italian_hilltop/source_2.mp4" width={240} controls muted />
  <Video src="/demos/live-photos/italian_hilltop/source_3.mp4" width={240} controls muted />
</div>

**Merged result (4.5 seconds of continuous footage):**

<Video src="/demos/live-photos/italian_hilltop/merged.mp4" width={720} controls />

---

**Bike race: 6 Live Photos merged into 8.4 seconds:**

<Video src="/demos/live-photos/bike_race/merged.mp4" width={720} controls />

## A Live Photo is a photograph

That is the whole model. A Live Photo's still arrives with the photographs and
competes as one. Whether its burst is worth showing **as motion** is a rendering
question, asked afterwards, about an asset that has already won its place.

It used to work the other way round: Live Photos were fetched separately, turned
into clips in a pool of their own, and their stills were removed from the photo
pool so the same instant would not ship twice. Anything that pool refused (a
burst too short to be worth stitching), then belonged to no pool at all and was
invisible to selection. That was the bug worth fixing; the counts that used to be
quoted here had no provenance and are gone.

## How it works

1. **Discovery**: Live Photo stills come back with the photographs. Under a person
   filter, only stills carrying the requested Immich person tag enter the pool
2. **Video components**: the video half of a Live Photo is dropped from the video pool; it is part of a photograph, not footage somebody shot
3. **Clustering**: photos taken within a configurable window (default 10.0s) form a burst
4. **Rendering choice**: a burst that stitches to at least `live_photo_min_clip_seconds` (default 3.5s) renders as motion; anything shorter renders as the photograph it is
5. **One carrier per burst**: a burst collapses to a single unit before the editor ever chooses. One photograph carries it (the favourite if there is one, otherwise the sharpest, best-exposed), and the siblings are not separately selectable
6. **Spectrogram alignment**: cross-correlates audio between overlapping clips to find the temporal offset, quantised to the STFT hop, about 5 ms
7. **Burst merging**: stitches clips with shutter-centered cuts, exposure normalization, and 30ms audio fade at boundaries

## Why 3.5 seconds

A lone Live Photo stitches to exactly 3.0s (the raw clip, with nothing merged),
while the smallest genuine merge of two reaches 4.0s. The threshold sits between
them, so a burst of one never displaces the photograph it would have shipped as.

Motion magnitude is deliberately **not** part of this. Measured over 64 real
bursts it correlates with something having happened (median 2.04 against 0.48)
but does not separate it: a baby's mouth closing scored 0.31 while the same
instant twice with a camera shift scored 0.63. Duration is structural and free;
motion is a signal for later, never a gate.

:::note Person-filtered memories
The person tag is the source boundary. An untagged Live Photo does not enter a
person memory merely because it was shot beside a tagged one. That can leave a
tagged frame without enough tagged neighbours to render as motion; it remains
selectable as a photograph instead of widening the memory to unrelated assets.
:::

## Burst merging: spectrogram-aligned shutter-centered cuts

When you rapid-fire photos, each Live Photo's video overlaps with the next. The merger uses **audio spectrogram fingerprinting** to find the exact overlap, then cuts at the midpoint between consecutive shutter presses.

### How the merged file is encoded

Bursts are merged while clips are still downloading, before the run has resolved the encoding plan
for its final video. The merge resolves its own: your hardware encoder if `hardware.enabled` is on
and a real test encode succeeded, software otherwise, at CRF 18.

It deliberately does not adopt the run's output settings. The merged file is an intermediate that
gets re-encoded during assembly, and an HLG burst in a memory you asked to output as SDR H.264 would
be tone-mapped here: before anything had decided to. So HDR bursts stay H.265 10-bit with their
transfer intact, and the assembler decides what to do with them later.

### Why audio alignment?

Timestamps alone aren't precise enough: each clip's video doesn't start at exactly `shutter_time - 1.5s`, and the drift is tens of milliseconds. On rapid bursts, that's enough to cause audible clicks and gaps.

The spectrogram (Short-Time Fourier Transform) creates a unique frequency fingerprint at every 5ms window. Even with repetitive beat-heavy music, the exact mix of frequencies is unique at each moment. Cross-correlating these fingerprints between clips gives an offset accurate to the hop size, about 5 ms. There is no confidence value: the best correlation wins.

### The algorithm

1. Extract 48kHz mono audio from each clip
2. Compute STFT spectrogram (1024-sample window, 256 hop)
3. For each consecutive pair: correlate the first 20 spectrogram frames of clip B, about 107 ms, against clip A to find where B's audio starts in A's timeline
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

Non-overlapping clips (gap > clip duration) are NOT merged: they stay as separate clips.

### Works for any phone with audio

The algorithm uses audio fingerprinting, not Apple metadata. It works for iPhone, Samsung, or any camera that records audio with video. The only requirement: overlapping clips with shared ambient audio.

Alignment is skipped when there is no audio to correlate, and also when the material is strict, when shutter timestamps are missing, or when there is only one clip. Nothing here keys on the device: a Pixel Motion Photo skips alignment because it carries no audio track, not because it is a Pixel. See [Device support](#device-support).

## Configuration

The `analysis.*` Live Photo keys and their defaults are in the
[config reference](../../reference/config-reference.md#video-analysis). The two that decide
anything: `live_photo_min_clip_seconds` (3.5) is the gate a Live Photo's motion has to clear before
its video is used at all, and `include_live_photos` (true) is the master switch. The CLI's
`--include-live-photos` cannot turn the feature back on when the config says false; both are ANDed.

## Device support

Immich normalizes Live Photos / Motion Photos across device types using the `livePhotoVideoId` field. The only device-specific branch in the code is for Google: a Pixel gets a shorter assumed clip duration for overlap detection. Everything else takes the Apple path, Samsung included.

| Feature | Apple iPhone and everything else | Google Pixel |
|---------|----------------------------------|--------------|
| Assumed clip duration for overlap | ~3.0s | 1.5s |
| Audio track | usually | **None** |
| Spectrogram alignment | Works | Skipped (no audio) |
| Burst merging | Overlapping clips merged | Each clip stays individual |

Real durations and frame rates are probed from the files themselves, not assumed per device.
Samsung Motion Photos take the Apple path and appear to work; nobody here owns one to check.

### Google Pixel (Motion Photos)

Google Pixel Motion Photos are fundamentally different: very short clips (0.7-1.3 seconds), no audio track, and no temporal overlap between consecutive shots. immich-memories detects Pixel clips via EXIF and:

1. **Uses a shorter assumed clip duration** (1.5s instead of 3.0s) for overlap detection. This is
   the only branch in the code that reads the device.
2. **Skips spectrogram alignment**: no audio means no spectral fingerprint to correlate, and the
   no-audio case is what the skip actually tests.

There is no force-merge path anywhere, for any device, so nothing is being switched off for Pixel:
4 rapid-fire Pixel photos become 4 individual clips because no rule was ever going to join them.

## When to enable

Live Photos are most useful when your library has lots of photos and relatively few videos. Burst merging is particularly effective for events where you took rapid-fire photos (birthdays, travel, kids playing): those bursts become one continuous clip, capped at 6 seconds in the cut, which carries the moment better than any single frame of it.

If your library already has plenty of video, live photos won't add much.
