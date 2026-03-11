---
sidebar_position: 4
title: Scene Detection
---

# Scene Detection

Chopping videos at fixed 5-second intervals is lazy and it shows. Scene detection finds where the camera actually cut (or where the scene changed significantly) and splits there instead.

## How it works

Uses [PySceneDetect](https://www.scenedetect.com/) under the hood. It analyzes frame-to-frame differences in color and luminance to identify scene boundaries. When the difference exceeds the threshold, that's a cut point.

## Configuration

```yaml
analysis:
  use_scene_detection: true    # enabled by default
  scene_threshold: 27.0        # default sensitivity
  min_segment_duration: 1.5    # seconds — drop anything shorter
  max_segment_duration: 10.0   # seconds — subdivide anything longer
```

### Threshold tuning

The `scene_threshold` controls sensitivity:

- **Lower values** (e.g., 20) — more sensitive, detects subtle lighting changes. Can over-segment.
- **Default (27.0)** — works well for typical home videos.
- **Higher values** (e.g., 35) — only detects hard cuts. Misses gradual transitions.

### Duration constraints

After scene detection splits the video:

- Segments shorter than `min_segment_duration` (1.5s) are discarded. These are usually flash frames or detection artifacts.
- Segments longer than `max_segment_duration` (10s) are subdivided into smaller chunks. A 25-second continuous shot becomes two or three segments.

## When scene detection is off

If you set `use_scene_detection: false`, the pipeline falls back to fixed-interval splitting. This is faster but produces worse results — you'll get cuts mid-sentence and mid-action.

## What happens next

After scene detection, each segment goes through [interest scoring](./smart-clip-selection.md) to decide which ones make it into the final video. Scene detection just finds the boundaries; scoring decides the quality.
