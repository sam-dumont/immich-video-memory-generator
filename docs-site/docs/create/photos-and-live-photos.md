---
sidebar_position: 6
title: Photos, Live Photos and HDR
---

import Video from '@site/src/components/Video';

# Photos, Live Photos and HDR

Photos compete in the same pool as videos. There is no separate photo pipeline: a still that wins a
slot is animated into a clip at assembly time and holds the screen for the seconds the editor
granted it. Every IMAGE asset in range comes back from Immich, Live Photo stills included.

`--no-photos` takes stills out of the pool for one run; `photos.enabled` is the persistent switch,
and a flag wins over it without changing anything on disk.

## Ken Burns, and where the pan lands

One renderer, every photo: a zoom of 5 to 12 % seeded from the asset ID so re-runs are stable,
panning toward the largest face Immich found, over a blurred copy of the photo itself when the
aspect ratios do not match. Pan direction is randomised per photo.

Faces steer exactly one thing: where that pan ends up. The face boxes come from Immich and nothing
in this app runs a detector to get them. With no faces on the asset the pan ends at the centre,
which is fine for landscapes, food and the dog.

Video clips are never cropped. When a landscape clip lands in a portrait video the whole frame is
kept and the leftover space is filled, using
[`scale_mode`](../reference/config-reference.md#generation-defaults): `blur` (the default) puts a
blurred, zoomed copy of the frame behind the sharp one, `fit` uses black bars. Face-aware cropping of
video is not selectable, because cropping a moving subject needs per-frame tracking and a smoothed
crop path rather than a single face position. An older `smart_crop` value in a config file loads as
`blur`, with a warning.

Older configs may also carry `collage_duration`, `animation_mode`, `enable_collage`,
`series_gap_seconds` or `zoom_factor`. Those five are ignored in silence: the zoom amount is
randomised per photo now and collages no longer exist. Four more went with the clip scorer and are
dropped by name with one warning: `max_ratio`, `read_moments`, `moment_gap_seconds` and
`moment_hash_threshold`.

## Live Photos

Every iPhone photo records about 3 seconds of video alongside it. Most people have thousands of
these in their library without knowing it, and a rapid-fire burst of them is several seconds of
continuous footage nobody meant to shoot.

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

**A Live Photo is a photograph.** Its still arrives with the photographs and competes as one.
Whether its burst is worth showing as motion is a rendering question, asked afterwards, about an
asset that has already won its place.

1. **Discovery**: Live Photo stills come back with the photographs. Under a person filter, only
   stills carrying the requested Immich person tag enter the pool
2. **Video components**: the video half is dropped from the video pool. It is part of a photograph,
   not footage somebody shot
3. **Clustering**: photos taken within a configurable window (default 10.0 s) form a burst
4. **Rendering choice**: a burst that stitches to at least `live_photo_min_clip_seconds` (default
   3.5 s) is a motion candidate; anything shorter renders as the photograph it is. The editor then
   measures how much actually moves (median optical flow over 12 frames). A candidate plays at 1.5
   or more, is offered to the story pick as motion and listed first beside the videos, and the next
   preparation writes it the same one-sentence motion line a video gets; below 1.5 it is offered and
   rendered as a photograph
5. **One carrier per burst**: a burst collapses to a single unit before the editor ever chooses. One
   photograph carries it, the favourite if there is one and otherwise the sharpest, best-exposed,
   and the siblings are not separately selectable

Why 3.5 seconds: a lone Live Photo stitches to exactly 3.0 s while the smallest genuine merge of two
reaches 4.0 s, so the threshold sits between them and a burst of one never displaces the photograph
it would have shipped as. Motion magnitude is deliberately not part of this. Measured over 64 real
bursts it correlates with something having happened (median 2.04 against 0.48) but does not separate
it: a baby's mouth closing scored 0.31 while the same instant twice with a camera shift scored 0.63.
Duration is structural and free. Motion is no gate: a quiet burst still ships as its photograph. It
decides whether a Live Photo plays, and a burst that plays is preferred over a still of the same
moment.

The two config keys that decide anything are `live_photo_min_clip_seconds` (3.5) and
`include_live_photos` (true). The CLI's `--include-live-photos` cannot turn the feature back on when
the config says false: both are ANDed. With merging turned off, a Live Photo is used as a still
rather than dropped.

:::note Person-filtered memories
The person tag is the source boundary. An untagged Live Photo does not enter a person memory merely
because it was shot beside a tagged one. That can leave a tagged frame without enough tagged
neighbours to render as motion; it stays selectable as a photograph rather than widening the memory
to unrelated assets.
:::

### Merging on audio, not timestamps

Each clip's video does not start at exactly `shutter_time - 1.5s`, and the drift is tens of
milliseconds, which on a rapid burst is enough for audible clicks and gaps. A Short-Time Fourier
Transform gives a frequency fingerprint every 5 ms, and the exact mix of frequencies is unique at
each moment even under repetitive beat-heavy music. Cross-correlating those fingerprints between
clips gives an offset accurate to the hop size. There is no confidence value: the best correlation
wins.

1. Extract 48 kHz mono audio from each clip
2. Compute an STFT spectrogram (1024-sample window, 256 hop)
3. For each consecutive pair, correlate the first 20 spectrogram frames of clip B, about 107 ms,
   against clip A to find where B's audio starts in A's timeline
4. Compute shutter-centered handoff points (the midpoint between consecutive shutters)
5. If a handoff falls before the next clip starts, extend the current clip to cover the hole
6. Trim each clip at its handoffs, normalize exposure, 30 ms audio fade at boundaries, concatenate

Three photos at t=0, t=0.5 s, t=2 s, each clip about 3 s:

| Clip | Plays from | Plays to | Duration |
|------|-----------|----------|----------|
| Photo 1 | start | midpoint(0, 0.5) = 0.25 s | ~1.75 s |
| Photo 2 | shutter-centered start | midpoint(0.5, 2.0) = 1.25 s | ~1.5 s |
| Photo 3 | shutter-centered start | end | ~1.5 s |

Clips that do not overlap (a gap longer than the clip duration) are not merged; they stay separate.
Alignment is skipped when there is no audio to correlate, when shutter timestamps are missing, or
when there is only one clip. Nothing here keys on the device: a Pixel Motion Photo skips alignment
because it carries no audio track, not because it is a Pixel.

Bursts are merged while clips are still downloading, before the run has resolved the encoding plan
for the final video, so the merge resolves its own: your hardware encoder if one passed a real test
encode, software otherwise, at CRF 18. It deliberately does not adopt the run's output settings,
because an HLG burst in a memory you asked to output as SDR H.264 would be tone-mapped here before
anything had decided to. HDR bursts stay H.265 10-bit with their transfer intact and the assembler
decides later.

### Devices

Immich normalizes Live Photos and Motion Photos across device types through `livePhotoVideoId`. The
only device-specific branch in the code is for Google: a Pixel gets a shorter assumed clip duration
for overlap detection, 1.5 s instead of 3.0 s. Everything else takes the Apple path, Samsung
included. Real durations and frame rates are probed from the files themselves.

A Pixel Motion Photo is 0.7 to 1.3 seconds, has no audio track, and does not overlap the next shot,
so four rapid-fire Pixel photos become four individual clips. There is no force-merge path anywhere,
for any device, so nothing is being switched off for Pixel: no rule was ever going to join them.

Burst merging pays where you fired off rapid shots (birthdays, travel, kids playing): the burst
becomes one continuous clip, capped at 6 seconds in the cut, which carries the moment better than
any single frame of it. If your library already has plenty of video, Live Photos will not add much.

## HDR, end to end

A memory keeps the dynamic range its sources have. HDR video (HLG or PQ from a phone) stays HDR, and
an HDR photograph is reconstructed from its gain map rather than flattened.

**Apple, iPhone 12 and later.** A HEIC carries an 8-bit Display P3 base image and a grayscale gain
map, and the headroom for the scene comes from two MakerNote tags read together, `0x0021` and
`0x0030`. The renderer interpolates linearly between 1.0 and that headroom,
`HDR = SDR x (1 + (headroom - 1) x gain)`, and ships 10-bit PQ, BT.2020. That is Apple's own shape
and it never darkens a pixel; the exponential `2^(gain x headroom)` form belongs to Ultra HDR and
lifted mid-tones about twice as far as CoreImage does. It takes both tags because `0x0021` alone
gave 2.01x on a photograph whose real headroom was 5.955x. Checked against CoreImage's own
`kCIImageExpandToHDR` on 11 photographs across headrooms 3.50 to 6.91: median error 1 to 2 %, 10 %
on the worst that converged. When the MakerNote does not parse it falls back to `exiftool` if
installed, and a photograph whose headroom cannot be read renders at the brightness of its base
image instead of black.

**Android Ultra HDR.** A JPEG with an MPF gain map and `hdrgm` XMP metadata, reconstructed with its
per-channel gamma and offsets.

HEIC is decoded with `pillow-heif`, because FFmpeg reads only the thumbnail tiles of a HEIC. Title
text over HDR is drawn at HLG graphics white rather than full white, so a caption does not glare
above the picture's own diffuse white.

For video the output follows the sources. `output.hdr_mode` is `auto` (HDR when any selected source
is HDR), `hdr` or `sdr`. HDR output is H.265 and only H.265: `encoding_plan.py` refuses `hdr` with
H.264, which carries no HDR at all, and refuses it with ProRes, so a ProRes render is SDR. Either
way it is refused rather than silently flattened. **HDR clips only** on the Memory page (`hdr_only`)
drops SDR video from the pool. Which backends take 10-bit is on
[Hardware acceleration](../deploy/hardware.md).
