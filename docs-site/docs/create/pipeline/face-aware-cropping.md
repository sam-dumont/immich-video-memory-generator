---
sidebar_position: 2
title: Face-Aware Framing
---

# Face-Aware Framing

Faces steer exactly one thing: where a photo's Ken Burns move ends up. They do **not** crop your
video clips; see [What happens to video clips](#what-happens-to-video-clips) below.

## Photos pan toward the face

Immich already knows where the faces are in a photo. The photo animator reads those boxes, picks
the largest face, and makes it the end point of the Ken Burns pan. So the move drifts toward the
person instead of drifting off into a wall.

With no faces on the asset, the pan ends at the centre: fine for landscapes, food shots, and the
dog.

That is the whole of it. The face boxes come from Immich; nothing in this app runs a detector to
get them.

## What faces no longer do

There used to be a per-clip scorer that counted faces per frame and let a segment with people in
it outscore an equally sharp segment of scenery. It is gone, along with every knob it read. Story-
first selection asks a different question (is this moment worth showing, and does this picture
carry it), and the count of recognised people is one column on the wall the reader sees, not a
term in a score.

The OpenCV and Apple Vision detection backends are still in the tree, behind a `smart_zoom` scale
mode that the config no longer accepts. Wired, but unreachable. Nothing in a real run calls
either.

## What happens to video clips

Nothing gets cropped. When a landscape clip lands in a portrait video, the whole frame is kept and
the leftover space is filled, using
[`scale_mode`](../../reference/config-reference.md#generation-defaults):

- `blur` (default): a blurred, zoomed copy of the frame sits behind the sharp one
- `fit`: black bars

Face-aware cropping of video is **not selectable**. Cropping a moving subject needs per-frame
tracking and a smoothed crop path, not a single face position, so the frame is kept whole instead
of guessing. An older `smart_crop` value in a config file loads as `blur`, with a warning.
