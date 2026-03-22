---
sidebar_position: 2
title: Face-Aware Cropping
---

# Face-Aware Cropping

When your source videos are landscape (16:9) but the output is portrait (9:16), or any other aspect ratio conversion, you lose a lot of the frame. Dumb center-cropping will cut people's heads off. Face-aware cropping detects faces first, then positions the crop window to keep them in frame.

## How it works

1. Detect faces in the frame
2. Calculate the bounding box that contains all detected faces
3. Position the crop window to center that bounding box
4. Clamp to frame boundaries (faces near the edge stay visible, the crop just shifts as far as it can)

If no faces are detected, it falls back to center crop: which is fine for landscapes, food shots, etc.

## Detection backends

The pipeline picks the face detection backend automatically based on your hardware:

| Platform | Backend | Speed |
|----------|---------|-------|
| macOS (Apple Silicon) | Apple Vision Framework (Neural Engine) | ~10x faster than OpenCV CPU |
| NVIDIA GPU | OpenCV CUDA | Fast, requires CUDA drivers |
| Everything else | OpenCV CPU (Haar cascades) | Works everywhere, just slower |

On a Mac with an M-series chip, the Vision Framework runs face detection on the Neural Engine, which is purpose-built for this kind of work. It's not just faster: it's more accurate too, especially with small or partially occluded faces.

## When it matters

Face-aware cropping kicks in during the assembly phase, whenever the source and target aspect ratios differ. If you're outputting at the same aspect ratio as your source, no cropping happens and this is a no-op.

The most common case: you shot everything in landscape on your phone, but want a vertical video for sharing. Without face-aware cropping, you'd lose whoever was standing on the left or right side of frame.
