---
sidebar_position: 4
title: Best Practices
---

# Best Practices

Things that save time and produce better results.

## Run Analysis First

Analysis is the slow part. Run it once and cache the results:

```yaml
analysis:
  use_scene_detection: true
```

Subsequent runs load from cache instantly. This matters a lot when you're iterating on clip selection.

## Use Hardware Acceleration

If you have a GPU, use it. The tool auto-detects NVIDIA (NVENC), Apple (VideoToolbox), Intel (QSV), and AMD (AMF). Check what's available:

```bash
immich-memories hardware
```

Encoding a 10-minute 1080p video takes ~30 seconds with NVENC vs ~5 minutes on CPU.

## Adjust Scene Detection Threshold

The default threshold (`27.0`) works for most content, but you might need to tune it:

- **Lower threshold** (e.g., `20.0`) = more scene cuts detected. Good for fast-paced content with lots of action.
- **Higher threshold** (e.g., `35.0`) = fewer cuts. Better for slow, steady footage like landscapes.

```yaml
analysis:
  scene_threshold: 27.0
```

## Start with Shorter Durations

Your first video should be 3-5 minutes, not 30. Shorter durations mean:

- Faster generation
- Easier to review
- Less wasted time if your settings are off

Once you're happy with the results, scale up.

## Review Clips Before Generating

Step 2 exists for a reason. Spend 2 minutes deselecting clips that don't belong — that shaky hallway video, the accidental recording of your pocket, the 45-second clip of a wall. The tool's scoring is good but not perfect.

## Enable LLM Analysis for Large Libraries

For libraries with hundreds of videos, LLM content analysis dramatically improves clip selection. It adds a few seconds per video to analysis time but catches things that motion/face detection misses — a quiet but meaningful conversation, a funny reaction shot, etc.

```yaml
content_analysis:
  enabled: true
  weight: 0.2
```

## Downscaling for Analysis

If analysis is slow, enable downscaling. The tool doesn't need full-resolution frames to detect scenes and score clips:

```yaml
analysis:
  enable_downscaling: true
  analysis_resolution: 480
```

This can cut analysis time in half with virtually no impact on clip selection quality.
