---
sidebar_position: 1
title: Clip Selection & Scoring
---

# Clip Selection & Scoring

The whole point of a memory video is picking the *good* parts. Nobody wants to watch 30 seconds of your pocket recording a sidewalk. The pipeline scores every segment across multiple factors, then picks the winners.

## The Density Budget

The selection algorithm distributes raw footage quotas across your timeline proportional to how many assets exist in each period. Months with more content (summer vacation, holidays, birthdays) automatically get more clips.

```
Target: 10-minute video → 550s content → 1100s raw footage budget

August (1200 assets, 7.3%):  80s quota  ← busy summer month
February (300 assets, 1.8%): 20s quota  ← quiet winter month
```

### What counts toward density

ALL asset types count equally toward a month's weight:
- Videos
- Photos (including HEIC/HEIF from iPhones)
- Live Photos

This means a month with 500 photos but few videos still gets proportional representation through animated photo clips.

## Scoring

Each asset gets a score from 0.0 to 1.0 that determines whether it makes the cut.

### Video Scoring

Videos are scored by analyzing their content. The base visual factors always sum to 1.0:

| Factor | Weight | How |
|--------|--------|-----|
| Face detection | 0.35 | Apple Vision or OpenCV face detection |
| Motion quality | 0.20 | Stable, intentional camera movement |
| Visual stability | 0.15 | Not shaky or blurry |
| Audio content | 0.15 | Laughter, speech, music detected |
| Duration fit | 0.15 | Clips near the optimal 5s duration score higher |

**LLM analysis** (when enabled) adds a **bonus on top** of the base score: it never reduces it. A content score above 0.5 (neutral) adds up to `content_analysis.weight` (default 0.35) as extra signal. This means LLM analysis can only improve clip selection, not hurt it.

### How scoring works in detail

Each video segment gets a composite interest score built from:

- **Face count and size**: segments with recognizable faces score higher. Bigger faces (closer shots) beat tiny background faces.
- **Motion intensity**: some movement is good (kids running around), too much usually means camera shake.
- **Stability**: smooth footage beats shaky footage. This is separate from motion: you can have smooth panning *and* high motion.
- **Content diversity**: the final selection balances variety. Three beach clips in a row get penalized in favor of mixing in different scenes.
- **LLM analysis** (optional): if you have a vision LLM configured, it adds a weighted semantic score. See [LLM Content Analysis](./llm-content-analysis.md).

### Photo Scoring

Photos use a mix of metadata and optional LLM visual analysis:

| Factor | Weight | How |
|--------|--------|-----|
| Base | 0.15 | Every photo starts here |
| Favorite | 0.25 | Favorited in Immich |
| Has faces | 0.15 | People detected by Immich |
| Face count | 0.10 | More faces = family moments (capped at 3+) |
| Camera original | 0.05 | Real camera EXIF (not screenshot) |
| LLM visual | 0.30 | VLM rates interest + quality |

Photo scores are multiplied by `(1 - score_penalty)` (default 0.8) so videos win ties.

### Live Photo Scoring

Live photos go through the same pipeline as videos after burst merging. A 0.9x penalty reflects that live photos are less intentional than deliberate recordings.

**Favorite inheritance**: If ANY photo in a burst cluster is favorited, the entire merged live photo clip inherits the favorite flag.

## Selection Process

```
1. Fetch ALL assets (videos + photos + live photos)
2. Thumbnail clustering → deduplicate near-identical clips
3. Compute density budget (quota per month/week)
4. Fill each bucket:
   a. Favorites first (capped at 1.5× quota to save LLM calls)
   b. Gap-fill with non-favorites ranked by score
5. Scene detection on selected clips (find best segments)
6. LLM analysis on favorites (rate interest/quality)
7. Final refinement: distribute by date, scale to target duration
8. Enforce photo cap (default 50% max)
```

## Analysis Depth

How much analysis effort to spend:

| Mode | Favorites | Gap-fillers | Speed |
|------|-----------|-------------|-------|
| **Fast** (default) | Full analysis + LLM | Metadata score only | Quick |
| **Thorough** | Full analysis + LLM | Top-3 per bucket get LLM too | Slower, better |

CLI: `--analysis-depth fast|thorough`

## Performance: 480p Downscaling

Videos are downscaled to 480p before analysis. This gives a 3-5x speedup over analyzing at full resolution, and for scoring purposes the quality difference is irrelevant. You're detecting faces and motion, not reading fine print.

## SQLite Caching

Once a clip has been analyzed, its scores are cached in SQLite. Re-running the pipeline on the same library skips all previously analyzed clips. This matters when you have thousands of videos: the first run might take a while, but subsequent runs only process new imports.

Only new or changed assets get re-analyzed. The cache also tracks the **scoring algorithm version**: when the scoring formula changes (e.g., after an update), old cached scores are automatically invalidated and clips get re-analyzed with the new algorithm. The cache persists across runs: back it up with your Docker volumes or Kubernetes PVCs.

## Scene Detection

Rather than chopping videos at fixed intervals, the pipeline uses [PySceneDetect](./scene-detection.md) to find natural scene boundaries. This means cuts happen where the camera already cut, not in the middle of someone's sentence.

## Duration Filtering

After scene detection, segments are filtered:

- **Minimum duration**: 2.0 seconds (default). Anything shorter is usually a flash or artifact.
- **Maximum duration**: 15.0 seconds (default). Longer scenes get subdivided to keep the final video punchy.

Both values are configurable in `analysis.min_segment_duration` and `analysis.max_segment_duration`.

## Clip Style Presets

Instead of tuning individual duration parameters, pick a preset:

| Preset | Vibe | Clip lengths |
|--------|------|-------------|
| `fast-cuts` | Energetic, music video feel | Short clips, frequent transitions |
| `balanced` | Default. Works for most memories | Mix of short and medium clips |
| `long-cuts` | Documentary, slow pacing | Longer clips, fewer cuts |

Set in config: `analysis.clip_style: balanced` (or pass no value to use individual duration params).

## Configuration

```yaml
photos:
  enabled: true           # Include photos (default: true)
  max_ratio: 0.50         # Max 50% of clips can be photos
  score_penalty: 0.2      # Photos score 80% of equivalent videos

scoring_priority:
  people: high      # Favor clips with recognized faces
  quality: medium   # Favor stable, well-lit footage
  moment: medium    # Favor interesting content (motion, events)
```
