---
sidebar_position: 5
title: Live Photos
---

import Video from '@site/src/components/Video';

# Live Photos

A Live Photo combines a still image with a short video. Immich Memories can use the still or, when nearby captures provide useful motion, merge the burst into a clip.

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

## How selection works

Live Photo stills enter the photograph pool. Their linked video components are removed
from the ordinary video pool so the same capture does not compete twice.

Photos captured close together can form a burst. One photograph represents it: a favourite
when available, otherwise the highest-quality frame. Its siblings stay attached to that
burst and are not independently selectable.

A burst must reach `analysis.live_photo_min_clip_seconds` (default 3.5 seconds) to offer
motion. That is only the first check. The editor also measures motion for selected
candidates; weak or unavailable motion leaves the photograph as a still. Motion units
are capped at six seconds before the cut's duration allocation. Quiet movement can therefore
remain a photograph even when its burst is long enough.

:::note Person-filtered memories
Only stills carrying the requested Immich person tags enter the pool. Untagged neighboring
frames are not added to complete a burst. This can leave a tagged frame without enough
motion, while keeping it eligible as a photograph.
:::

## How bursts are rendered

The editor records which source videos and shutter-based intervals belong to a selected
burst. Rendering uses those intervals, with frame-boundary rounding, and validates the
result against the selected material. It does not shift the cuts using a second audio
alignment pass.

The merge normalizes exposure and fades audio at joins when every source has audio.
A missing required source or invalid interval fails the render rather than silently
substituting different footage.

The intermediate uses a working hardware encoder when `hardware.enabled` is on, software
otherwise. HDR transfer is retained in that intermediate; final assembly applies the
requested output codec and HDR mode. See [HDR](./hdr.md).

## Configuration

```yaml
analysis:
  include_live_photos: true                # ON by default
  live_photo_merge_window_seconds: 10.0    # Max gap between photos to form a burst
  live_photo_min_clip_seconds: 3.5         # Shorter than this, it ships as a photograph
```

Two sufficiently close captures can form a burst. There is no configurable minimum count.

In the web UI it is the **Include Live Photos** switch under Advanced on the Memory page. Via CLI:

```bash
immich-memories generate --include-live-photos --year 2024
```

## Device support

Immich exposes linked motion through `livePhotoVideoId`. Actual companion durations
are read from Immich metadata and validated against downloaded media. When duration
metadata is unavailable, the photograph can remain eligible without a motion offer.

Audio, duration and overlap vary by file. Do not assume that every iPhone, Samsung or
Pixel capture supplies the same length or an audio track. Short or non-overlapping
captures often remain stills; enabling Live Photos does not guarantee motion in the cut.
