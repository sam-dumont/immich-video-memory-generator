# How Clips Are Selected

This page explains how immich-memories decides which videos, photos, and live photos make it into your memory compilation.

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

**LLM analysis** (when enabled) adds a **bonus on top** of the base score — it never reduces it. A content score above 0.5 (neutral) adds up to `content_analysis.weight` (default 0.35) as extra signal. This means LLM analysis can only improve clip selection, not hurt it.

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

Live photos go through the same pipeline as videos after burst merging. A 0.9× penalty reflects that live photos are less intentional than deliberate recordings.

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
| **Thorough** | Full analysis + LLM | Top-3 per bucket → LLM | Slower, better |
| **Analyze All** | Everything | Everything | Slowest, maximum quality |

CLI: `--analysis-depth fast|thorough`
GUI: "Analyze all videos" button for full override.

## Score Caching

LLM analysis results are cached in the local SQLite database (`~/.immich-memories/cache.db`). First run analyzes everything (slow). Subsequent runs hit the cache (instant).

Only new or changed assets get re-analyzed. The cache also tracks the **scoring algorithm version** — when the scoring formula changes (e.g., after an update), old cached scores are automatically invalidated and clips get re-analyzed with the new algorithm. The cache persists across runs — back it up with your Docker volumes or Kubernetes PVCs.

## Configuration

```yaml
photos:
  enabled: true           # Include photos (default: true)
  max_ratio: 0.50         # Max 50% of clips can be photos
  score_penalty: 0.2      # Photos score 80% of equivalent videos

analysis:
  analysis_depth: fast    # fast | thorough
```
