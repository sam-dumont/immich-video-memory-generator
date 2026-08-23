---
sidebar_position: 2
title: Face-Aware Framing
---

# Face-Aware Framing

Faces steer two things: which clips get picked, and where a photo's Ken Burns move ends up. They do **not** crop your video clips — see [What happens to video clips](#what-happens-to-video-clips) below.

## Photos pan toward the face

Immich already knows where the faces are in a photo. The photo animator reads those boxes, picks the largest face, and makes it the end point of the Ken Burns pan. So the move drifts toward the person instead of drifting off into a wall.

With no faces on the asset, the pan ends at the centre — fine for landscapes, food shots, and the dog.

## Faces feed clip scoring

Clip scoring counts faces per frame: a segment with people in it outscores an equally sharp segment of scenery. The pipeline picks the detection backend automatically:

| Platform | Backend | Speed |
|----------|---------|-------|
| macOS (Apple Silicon) | Apple Vision Framework (Neural Engine) | ~10x faster than OpenCV CPU |
| Everything else (including NVIDIA/Intel/AMD boxes) | OpenCV CPU (Haar cascades) | Works everywhere, just slower |

There is no CUDA face-detection path; on Linux, GPUs are used for encoding, scaling and (with a CUDA build of OpenCV) scene analysis, not for faces.

On a Mac with an M-series chip, the Vision Framework runs face detection on the Neural Engine, which is purpose-built for this kind of work. It's not just faster: it's more accurate too, especially with small or partially occluded faces.

## What happens to video clips

Nothing gets cropped. When a landscape clip lands in a portrait video, the whole frame is kept and the leftover space is filled, using [`scale_mode`](../../reference/config-reference.md#generation-defaults):

- `blur` (default) — a blurred, zoomed copy of the frame sits behind the sharp one
- `fit` — black bars

Face-aware cropping of video is **not implemented**. Cropping a moving subject needs per-frame tracking and a smoothed crop path, not a single face position, so the frame is kept whole instead of guessing. The face-centre helpers in `processing/scaling_utilities.py` are parked there for whoever builds it.
