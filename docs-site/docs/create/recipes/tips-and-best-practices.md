---
sidebar_position: 3
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

Step 2 exists for a reason. Spend 2 minutes deselecting clips that don't belong: that shaky hallway video, the accidental recording of your pocket, the 45-second clip of a wall. The tool's scoring is good but not perfect.

## Enable LLM Analysis for Large Libraries

For libraries with hundreds of videos, LLM content analysis makes a real difference in clip selection. It adds a few seconds per video to analysis time but catches things that motion/face detection misses: a quiet but meaningful conversation, a funny reaction shot, etc.

```yaml
content_analysis:
  enabled: true
  weight: 0.35
```

## Downscaling for Analysis

Already on. `enable_downscaling` defaults to `true` and `analysis_resolution` to 480, so
every run already scores clips off a 480p proxy rather than the 4K source — the tool does
not need full-resolution frames to detect scenes or rank clips.

```yaml
analysis:
  enable_downscaling: true   # default
  analysis_resolution: 480   # default
```

The only reason to touch these is to go the other way: raise `analysis_resolution` if you
think scoring is missing small faces in wide shots, and accept the slower analysis.
