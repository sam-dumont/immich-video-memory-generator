---
sidebar_position: 1
title: Smart Clip Selection
---

# Smart Clip Selection

The whole point of a memory video is picking the *good* parts. Nobody wants to watch 30 seconds of your pocket recording a sidewalk. The clip selection pipeline scores every segment across multiple factors, then picks the winners.

## How scoring works

Each video segment gets a composite interest score built from:

- **Face count and size**: segments with recognizable faces score higher. Bigger faces (closer shots) beat tiny background faces.
- **Motion intensity**: some movement is good (kids running around), too much usually means camera shake.
- **Stability**: smooth footage beats shaky footage. This is separate from motion, you can have smooth panning *and* high motion.
- **Content diversity**: the final selection balances variety. Three beach clips in a row get penalized in favor of mixing in different scenes.
- **LLM analysis** (optional): if you have a vision LLM configured, it adds a weighted semantic score. See [LLM Content Analysis](./llm-analysis.md).

## Performance: 480p downscaling

Videos are downscaled to 480p before analysis. This gives a 3-5x speedup over analyzing at full resolution, and for scoring purposes the quality difference is irrelevant. You're detecting faces and motion, not reading fine print.

## SQLite caching

Once a clip has been analyzed, its scores are cached in SQLite. Re-running the pipeline on the same library skips all previously analyzed clips. This matters when you have thousands of videos: the first run might take a while, but subsequent runs only process new imports.

## Scene detection

Rather than chopping videos at fixed intervals, the pipeline uses [PySceneDetect](./scene-detection.md) to find natural scene boundaries. This means cuts happen where the camera already cut, not in the middle of someone's sentence.

## Duration filtering

After scene detection, segments are filtered:

- **Minimum duration**: 2.0 seconds (default). Anything shorter is usually a flash or artifact.
- **Maximum duration**: 15.0 seconds (default). Longer scenes get subdivided to keep the final video punchy.

Both values are configurable in `analysis.min_segment_duration` and `analysis.max_segment_duration`.

## Clip style presets

Instead of tuning individual duration parameters, pick a preset:

| Preset | Vibe | Clip lengths |
|--------|------|-------------|
| `fast-cuts` | Energetic, music video feel | Short clips, frequent transitions |
| `balanced` | Default. Works for most memories | Mix of short and medium clips |
| `long-cuts` | Documentary, slow pacing | Longer clips, fewer cuts |

Set in config: `analysis.clip_style: balanced` (or pass no value to use individual duration params).

## Scoring priority

Three knobs control what the scoring algorithm values most:

```yaml
scoring_priority:
  people: high      # Favor clips with recognized faces
  quality: medium   # Favor stable, well-lit footage
  moment: medium    # Favor interesting content (motion, events)
```

For a family birthday compilation, `people: high` makes sense. For a landscape trip, try `people: low, moment: high`.
