---
sidebar_position: 6
title: Photos, Live Photos and HDR
---

import Video from '@site/src/components/Video';

# Photos, Live Photos and HDR

Reader: power user. How a still becomes a shot, when a Live Photo plays as motion, and how HDR
survives from the phone to the film.

Photos compete in the same pool as videos, on every tier. There is no separate photo pipeline: a
still that wins a slot is animated at render time and holds the screen for the seconds the editor
gave it. Every image in range comes back from Immich, Live Photo stills included.

`--no-photos` takes stills out for one run; `photos.enabled` is the lasting switch.

## Ken Burns, and where the pan lands

One renderer for every photo: a zoom of 5 to 12 %, seeded from the asset ID so a re-render moves the
same way, panning toward the largest face Immich found. With no face on the picture the pan ends at
the centre, which suits landscapes, food and the dog. The face boxes come from Immich; framing runs
no face detector of its own.

Video clips are never cropped. A landscape clip in a portrait film keeps its whole frame and
[`scale_mode`](../reference/config-reference.md) fills the rest: `blur` (default) puts a blurred, zoomed
copy behind the sharp one, `fit` uses black bars. Face-aware video cropping is not offered: a moving
subject needs per-frame tracking, not one face position. An old `smart_crop` value loads as `blur`
with a warning.

## Live Photos

An iPhone records about 3 seconds of video with every photo. Most libraries hold thousands of them,
and a rapid burst of them is several seconds of continuous footage nobody meant to shoot.

Three photos of an Italian hilltop, fired off in a row, each about 3 seconds and overlapping:

<div style={{display: 'flex', gap: '8px', flexWrap: 'wrap'}}>
  <Video src="/demos/live-photos/italian_hilltop/source_1.mp4" width={240} controls muted />
  <Video src="/demos/live-photos/italian_hilltop/source_2.mp4" width={240} controls muted />
  <Video src="/demos/live-photos/italian_hilltop/source_3.mp4" width={240} controls muted />
</div>

Merged, 4.5 seconds of continuous footage:

<Video src="/demos/live-photos/italian_hilltop/merged.mp4" width={720} controls />

A bike race, 6 Live Photos merged into 8.2 seconds:

<Video src="/demos/live-photos/bike_race/merged.mp4" width={720} controls />

These clips are the project author's own footage, published with permission.

**A Live Photo is a photograph.** Its still competes with the photographs and wins or loses as one.
Whether it plays as motion is a rendering question, asked afterwards, about a picture that has
already won its place. The answer is the same on every tier, model or not:

```mermaid
flowchart TD
  L[Live Photo kept in the cut] --> B{Other Live Photos<br/>within 10 s?}
  B -- no, lone --> R
  B -- yes, a burst --> J{Stitched burst at least 3.5 s?<br/>live_photo_min_clip_seconds}
  J -- no --> S[Plays as its still]
  J -- yes --> R{Motion residual at least 1.5?<br/>RESIDUAL_MIN}
  R -- no --> S
  R -- yes --> F{Clip shows the subject?<br/>clip_frames}
  F -- often missing --> S
  F -- yes --> M[Plays as motion, up to 6 s]
```

1. **The video half is not footage.** It leaves the video pool: it belongs to a photograph.
2. **Bursts.** Photos within `live_photo_merge_window_seconds` (10 s) of each other form a burst. A
   burst collapses to one unit before the editor chooses: the favourite carries it, otherwise the
   sharpest, best-exposed frame, and the siblings are not separately selectable.
3. **Length.** A lone Live Photo is a motion candidate. A join of two or more must stitch to at
   least `live_photo_min_clip_seconds` (3.5 s), because a stitch shorter than that is more cut than
   footage. A lone clip runs 1.7 to 3.3 s, so the rule only applies to joins.
4. **Motion.** The residual is the optical flow left once the camera's own movement is removed,
   over 12 frames. At 1.5 or above something happened in every burst we measured; below it the
   answer is a coin flip, and a still always works where a dead clip does not.
5. **The subject.** Where the clip's frames were read and the subject is often out of frame (the
   phone already on its way to the pocket), the picture plays as its still.

A clip only ever costs a picture its motion, never its place, favourite or not. A Live Photo that
plays is offered beside the videos, and a true video always plays whatever its residual.

The keys that decide anything are `include_live_photos` (true) and `live_photo_min_clip_seconds`
(3.5). `--include-live-photos` cannot turn the feature on when the config says false: both must
agree. With it off, a Live Photo is used as its still, never dropped.

A shared album can hold a downscaled copy of a Live Photo pointing at the same video. The video
belongs to both copies, so whichever the cut keeps plays it.

:::note Person-filtered memories
The person tag is the boundary. An untagged Live Photo does not enter a person memory because it was
shot beside a tagged one. A tagged frame can then lack neighbours to render as motion; it stays a
photograph rather than widening the memory to pictures without that person.
:::

### Merging on audio, not timestamps

A clip's video does not start at exactly `shutter_time - 1.5 s`, and tens of milliseconds of drift
on a rapid burst means audible clicks and gaps. So the joins are measured, and only for the bursts
the film keeps: the draft plans every burst from its metadata, then a kept burst's clips are
downloaded once and lined up on their sound. The answer is banked per pair, so the next film over the
same pictures downloads nothing.

1. Extract 48 kHz mono audio from each clip
2. Compute an STFT spectrogram (1024-sample window, 256 hop, one fingerprint every 5 ms)
3. For each pair, correlate the first 20 frames of clip B (about 107 ms) against clip A
4. Cut at the midpoint between consecutive shutters; extend a clip to cover any hole
5. Normalize exposure, fade the audio 30 ms at each join, concatenate

Three photos at t = 0, 0.5 s and 2 s, each clip about 3 s:

| Clip | Plays from | Plays to | Duration |
|------|-----------|----------|----------|
| Photo 1 | start | midpoint(0, 0.5) = 0.25 s | ~1.75 s |
| Photo 2 | shutter-centred start | midpoint(0.5, 2.0) = 1.25 s | ~1.5 s |
| Photo 3 | shutter-centred start | end | ~1.5 s |

Clips that do not overlap stay separate. With no shared content to line up on, nothing is stitched:
the kept picture plays its own clip if that moves, and its still otherwise. The merge encodes at CRF
18 with your hardware encoder if one passed a test encode, software otherwise. HDR bursts stay H.265
10-bit with their transfer intact; the final render decides the rest.

### Devices

Immich normalizes Live Photos and Motion Photos through `livePhotoVideoId`. The one device branch is
Google: a Pixel gets a 1.5 s assumed clip length instead of 3.0 s. A Pixel Motion Photo is 0.7 to
1.3 s with no audio track and does not overlap the next shot, so four rapid Pixel photos stay four
clips. Everything else, Samsung included, takes the Apple path.

## HDR, end to end

A film keeps the dynamic range its sources have. HDR video (HLG or PQ) stays HDR, and an HDR
photograph is rebuilt from its gain map rather than flattened.

**Apple, iPhone 12 and later.** A HEIC carries an 8-bit Display P3 image and a grayscale gain map;
the headroom comes from two MakerNote tags read together, `0x0021` and `0x0030`. The renderer applies
`HDR = SDR x (1 + (headroom - 1) x gain)` and ships 10-bit PQ, BT.2020, which is Apple's own shape
and never darkens a pixel. When the MakerNote does not parse it falls back to `exiftool` if
installed; a photograph whose headroom cannot be read renders at the brightness of its base image.

**Android Ultra HDR.** A JPEG with an MPF gain map and `hdrgm` XMP metadata, rebuilt with its
per-channel gamma and offsets.

`pillow-heif` decodes HEIC, because FFmpeg only reads a HEIC's thumbnail tiles. Title text
over HDR is drawn at HLG graphics white, so a caption does not glare above the picture.

`output.hdr_mode` is `auto` (HDR when any selected source is HDR), `hdr` or `sdr`. HDR output is
H.265 only: `hdr` with H.264 or ProRes is refused, never silently flattened. **HDR clips only** on
the Memory page (`hdr_only`) drops SDR video from the pool. Which encoders take 10-bit is on
[Hardware encoding](../run/hardware.md).
