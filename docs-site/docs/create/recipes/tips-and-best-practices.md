---
sidebar_position: 4
title: Tips & Best Practices
---

# Tips & Best Practices

Things that save time and produce better results.

## Run Analysis First

Analysis is the slow part on a cold library. `analyze` does it on its own, without
rendering anything:

```bash
immich-memories analyze --year 2024
```

Subsequent generate runs read from that cache instead of re-analysing, which is what
makes iterating on clip selection quick. On a NAS this is the thing to run overnight.
See [Discovery & utility commands](../cli/discovery-and-utility.md).

## Use Hardware Acceleration

If you have a GPU, use it. The tool auto-detects NVIDIA (NVENC), Apple (VideoToolbox), Intel (QSV), and AMD (VAAPI). Check what's available:

```bash
immich-memories hardware
```

Encoding 1080p runs at about 2 minutes per 5 minutes of output on Apple Silicon or a GPU; a 30-clip video takes around 15 minutes on a 4-core NAS CPU. See the [resource table](https://github.com/sam-dumont/immich-video-memory-generator#resource-requirements).

## Start with Shorter Durations

Your first video should be 3-5 minutes, not 30. Shorter durations mean:

- Faster generation
- Easier to review
- Less wasted time if your settings are off

Once you're happy with the results, scale up.

## Exclude Before You Cut

The media pool (**Advanced → Open the media pool** on the Memory page) exists for a reason. Spend 2 minutes unticking what may never be used: the accidental recording of your pocket, the 45-second clip of a wall. The editor judges twins and bursts itself; what it cannot know is what you would never show.

## Set Up the Annotation Producers First

The story-first route needs its caption endpoint, the pinned encoder and the two detectors before its first cut; a missing producer stops the run with a count rather than quietly narrowing what the editor sees. Do the [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md) once, then forget about it.

## Nothing Reads the 4K Source Until the Render

Captions are read from 400 px tiles and pixel facts from one fixed JPEG recipe; the editor
never needs full-resolution frames to know what a picture shows. The originals are downloaded
once, for the clips that made the cut, at render time.
