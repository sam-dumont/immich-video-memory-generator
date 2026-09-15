---
sidebar_position: 4
title: Before the first long run
---

# Before the first long run

Four things worth knowing before you set a slow host going for the night.

## There is no pre-warm command

`analyze` counts videos; it prepares nothing. The only thing that fills the annotation banks is a
cut. So on a slow host, run the cut you actually want overnight instead of trying to warm up first,
and lean on the fact that a second cut over the same period, or one that overlaps it, skips the
work already done.

Do the [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md) once
before that first cut. A missing producer stops the run with a count rather than quietly narrowing
what the editor sees.

## Nothing reads the 4K source until the render

Captions are read from 400 px tiles and pixel facts from one fixed JPEG recipe; the editor never
needs full-resolution frames to know what a picture shows. The originals are downloaded once, for
the clips that made the cut, at render time.

## Make the first one short

Three to five minutes, not thirty. If the config is wrong you find out in a quarter of the time.

## Untick what you would never show

Two minutes in the [media pool](../web-ui/memory.mdx#the-media-pool-page) keeps the pocket
recording and the 45-second clip of a wall out of the editor's hands. Twins and bursts it judges
itself; what it cannot know is what you would never show anyone.

For what a GPU is and is not worth here, the measured numbers are in
[Hardware acceleration](../../deploy/hardware/overview.md#what-the-card-is-actually-worth).
