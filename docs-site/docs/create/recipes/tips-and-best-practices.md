---
sidebar_position: 4
title: Tips & Best Practices
---

# Tips & Best Practices

Things that save time and produce better results.

## Warm the banks with a real cut

Preparation is the slow part on a cold library, and there is no command that does it on its own:
`analyze` counts videos, it does not prepare anything. The only thing that fills the banks is a
cut. So on a NAS, run the cut you want overnight rather than trying to pre-warm it, and take
advantage of the fact that a second cut over the same period, or an overlapping one, skips the
work. See [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md).

## Use Hardware Acceleration

If you have a GPU, use it. The tool auto-detects NVIDIA (NVENC), Apple (VideoToolbox), Intel (QSV), and AMD (VAAPI). Check what's available:

```bash
immich-memories hardware
```

The one measured run is in the [NAS guide](../../deploy/common-setups/nas-only.md#preparation-tiers-what-the-nas-pays): a 14-clip monthly on four cores with no GPU, 2.7 minutes of render under `preset: fast`. Most of that is title screens, which is the part a GPU actually shortens.

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
