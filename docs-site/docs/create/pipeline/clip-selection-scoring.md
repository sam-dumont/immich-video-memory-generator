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

Live photos go through the same pipeline as videos after burst merging and are scored the same way.

**Favorite inheritance**: If ANY photo in a burst cluster is favorited, the entire merged live photo clip inherits the favorite flag.

## What a Clip Is Of

A memory is about the people in it. Scoring ranks clips on faces, motion, stability
and cut quality, so a steady handheld pan across a lawn can outrank a shaky clip of
a child — a real generation put a string trimmer in a family video that way.

Every candidate is therefore categorised as **people**, **animal**, **landscape** or
**object** before selection, using the most trustworthy signal available:

1. **Immich face tags.** Face recognition has already run over your library. A clip
   with a tagged person is people, no model call needed. This covers roughly half a
   typical pool and works on already-cached clips.
2. **The category the model picks.** Content analysis asks the VLM to choose one of
   the four. This fills in as clips are analysed — existing cache entries keep
   working through step 3 until they are re-analysed.
3. **Keywords in the description**, for clips analysed before the model was ever
   asked for a category.

The quotas:

| category | treatment |
|---|---|
| people | always eligible |
| animal | up to `max_animal_ratio` of the video (default 10%) |
| object | up to `max_object_ratio` (default 5%) **and** must beat the median people clip |
| landscape | must beat the median people-clip score |
| unknown | always kept |

Quotas are a **share of the finished video**, not a fixed count, so a ten-minute
memory gets a proportionally larger allowance than a sixty-second one. The expected
clip count is estimated from the runtime budget and the typical candidate length,
because the candidate pool is many times larger than the final selection. Any
non-zero ratio yields at least one slot; a ratio of `0` means none at all.

Objects are rationed rather than banned. Buying a new car is a memory and a
lawnmower is not, and the thing separating them is whether the clip is any good — so
objects must clear the same bar as scenery *and* fit the quota.

That bar is the median people-clip score rather than a fixed number — and it is
computed **separately for photos and for motion clips**, because the two are scored
by different pipelines and land in different ranges. On a real June pool, people
motion clips sat at a 0.70 median while photos sat at 0.43. Pooling both put the bar
at 0.43, low enough for a clip of a string trimmer scoring 0.61 to clear it; judged
against its own scale, it does not.

A clip nobody has described yet is **kept**. On a real library 35–46% of the pool
has no cached description, and treating that silence as "probably an object" would
delete half the memory. If the quotas would empty the pool entirely — an all-scenery
trip — the policy stands down and logs that it did. A shorter video is the goal; an
empty one is a failure.

Set `subject_policy_enabled: false` to turn the whole thing off.

## Selection Process: Unified Pool

Videos, live photos, and regular photos all compete in a single selection pool. There are no separate pipelines — temporal dedup, duration scaling, and all caps apply to the combined pool.

```
1. Fetch videos + live photo video components
2. Fetch regular photos (IMAGE assets, excluding live photos)
3. VIDEOS: SmartPipeline Phases 1-3
   a. Apply hard exclusions only: unchecked media, true duplicates, unusably short clips, and an explicit HDR-only mismatch
   b. Density budget → choose a bounded shortlist for expensive scene/VLM analysis
   c. Give every other eligible video a cached or metadata-based fallback segment
4. PHOTOS: Score every eligible photo from metadata, run VLM scoring on a distributed shortlist, then merge the enhanced scores back into the full photo pool
5. MERGE: Convert all eligible scored photos to clip candidates and combine them with videos and Live Photos
6. UNIFIED Phase 4: Select from the combined pool
   a. Favorites first, then fill gaps by score
   b. Temporal coverage: ensure every month/week has ≥1 clip
   c. Scale to target duration (sole monthly representatives protected)
   d. Temporal dedup (same-moment clips across ALL types)
   e. Prefer variety, then progressively relax preferences when the timeline is short
```

The Step 2 checkboxes define the source pool. **Fast** means “deeply analyze fewer videos,” not
“throw the rest away.” The completion summary reports eligible media, videos deeply analyzed, and
clips finally planned as separate numbers.

### Sparse Content Adaptations

When content is limited, the pipeline adapts automatically:

- **Media-aware trip Auto duration**: The editorial curve is 30 seconds plus 10 seconds per active day, bounded to 60–300 seconds for dense trips. Usable video excerpts, at most four photos per day for capacity estimation, and a 30-second/day diversity ceiling can lower it. Sparse trips may resolve below 60 seconds.
- **Auto LLM budget**: Auto runs LLM analysis for every eligible clip when at most 60 clips need fresh analysis. For larger libraries it uses a time-balanced shortlist. Compatible current-model cache hits do not consume this budget.
- **Temporal coverage**: Every time period gets at least one clip. Sole monthly representatives are protected from removal during duration scaling, even if they score lower than favorites in other months.
- **Progressive backfill**: The selector first uses strict preferences, then allows up to 70% photos, additional non-favorites, closer moments, and finally any eligible photo ratio. If the only remaining clip is slightly too long, it may accept up to two seconds of overrun for the renderer to trim.
- **Soft diversity limits**: Two photos per day, photo ratio, non-favorite ratio, and temporal spacing are preferred-first rules. They can be exceeded to fill the requested duration. Hard exclusions are never relaxed.

### Live Photo Rendering

When a live photo (IMAGE asset with a video component) is selected, the actual video component is used — 2-7 seconds of real camera motion. Only truly static photos (no video component) get the Ken Burns animation treatment.

## Analysis Depth

How much analysis effort to spend:

| Mode | Favorites | Gap-fillers | Speed |
|------|-----------|-------------|-------|
| **Auto** (default) | Every eligible clip for manageable cache-miss pools | Time-balanced LLM shortlist for large pools | Adaptive |
| **Fast** | Full analysis + LLM | Local scoring/cached metadata | Quick |
| **Thorough** | Full analysis + LLM | Full analysis + LLM | Slowest, exhaustive |

CLI: `--analysis-depth auto|fast|thorough`

Cache reuse is model-aware. Results from the exact configured model are loaded automatically and
shown in review; they skip another LLM request. Results with no model identity or from a different
model are stale and are analyzed again.

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
```
