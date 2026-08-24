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

Photo scores are multiplied by `(1 - score_penalty)`. The default penalty is 0.2, so a photo
scores 80% of an equally good video and videos win ties.

### Live Photo Scoring

Live photos go through the same pipeline as videos after burst merging and are scored the same way.

**Favorite inheritance**: If ANY photo in a burst cluster is favorited, the entire merged live photo clip inherits the favorite flag.

## Source Quality

Messaging apps re-encode video to a few hundred pixels and strip the camera EXIF on
the way through. A clip whose short side is under `min_source_short_side` (default
1080) is dropped **unless it carries a camera make or model**, which is what
separates a WhatsApp forward from genuinely old footage that was always small.

Measured on a 111-clip June pool: all 17 sub-1080p candidates had no camera EXIF
whatsoever, and all 89 with camera EXIF were 1080p or better.

## What a Clip Is Of

A memory is about the people in it. Scoring ranks clips on faces, motion, stability
and cut quality, so a steady handheld pan across a lawn can outrank a shaky clip of
a child — a real generation put a string trimmer in a family video that way.

Every candidate is therefore categorised as **people**, **animal**, **landscape**,
**object** or **screen** before selection, from two signals only:

1. **Immich face tags.** Face recognition has already run over your library. A clip
   with a tagged person is people, no model call needed — this covers roughly half a
   typical pool.
2. **The category the model picks**, from that closed set, as part of content
   analysis.

Nothing else decides. An earlier version matched keywords in the model's written
description and it was wrong in every interesting case: a treadmill and a
driver's-eye road view became "landscape" because both descriptions said *close-up
view*; a tray of animal figurines became "animal"; a smartwatch demo became "people"
because a person was wearing the watch. Prose is not a label. A clip the model has
not labelled is **unknown**, and unknown is kept.

The quotas:

| category | treatment |
|---|---|
| people | always eligible |
| animal | up to `max_animal_ratio` of the video (default 10%) |
| object | up to `max_object_ratio` (default 5%) **and** must beat the median people clip |
| screen | never — a screenshot, a phone or watch display, or a document is not a memory |
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
   a. Apply hard exclusions only: unchecked media, true duplicates, unusably short clips, media the library's own camera did not shoot, and an explicit HDR-only mismatch
   b. Density budget → choose a bounded shortlist for expensive scene/VLM analysis
   c. Give every other eligible video a cached or metadata-based fallback segment
4. PHOTOS: Drop what the camera did not shoot, score every eligible photo from metadata, run VLM scoring on a distributed shortlist, then merge the enhanced scores back into the full photo pool
5. MERGE: Convert all eligible scored photos to clip candidates and combine them with videos and Live Photos
6. UNIFIED Phase 4: Select from the combined pool
   a. Favorites first, then fill gaps by score
   b. Temporal coverage: ensure every month/week has ≥1 clip
   c. Scale to target duration (sole monthly representatives protected)
   d. Temporal dedup (same-moment clips across ALL types), with the window measured against the memory's span
   e. Prefer variety, then progressively relax preferences when the timeline is short
7. STABILISE: verify → judge → review, looping until the cut stops changing
```

### Phase 5: the cut has to survive being looked at

Selecting is not the last word. A clip can reach the final cut without anything having actually
looked at it, and a cut that scores well can still be repetitive. Three stages run after
selection, and every drop re-runs selection — which admits new clips that nothing has judged
yet, which is why they loop.

**Verify** catches clips nobody looked at. Two ways that happens: the clip carries a metadata
guess instead of a real score, or it has a real visual score but no content analysis, so the
review would be handed a bare line and — correctly — told never to drop a clip for missing
information. Either way the clip is analysed for real and selection re-runs. Cold, that is a
download and a full analysis; warm, it is a cache hit. Photographs are never queued here: their
real look is the photo scorer, which has already run.

**Judge** is mechanical and cheap — thresholds, no model. A non-favourite scoring below
`judge_floor_score` (0.30) never ships. Separately, the chronologically last clip cannot be both
the weakest in the cut and below `judge_boundary_ratio` (0.6) of the mean, because a video should
not end on its worst shot. Favourites are exempt from both rules: the user chose them, and
"start with all favourites" is the selection's oldest contract.

**Review** is one LLM call over the whole cut — every clip's description, emotion, setting,
subjects, audio categories, date and place, in timeline order. It is asked which clips are
redundant, which subject is crowding out the rest, which clip clashes, and which is not a memory
at all. It can drop at most 20% of the selection in one pass, never a favourite, and any failure
or unparseable answer drops nothing. It is **optional by construction** — with no LLM configured
it returns no drops and the selection is unchanged.

`analysis.max_refinement_passes` (default 10) bounds each of these loops. It is the single
largest multiplier on what a warm run costs, because each review pass is an uncached LLM call —
the per-clip analysis is cached, but the review reads the current selection, which changes every
round, so there is nothing to key a cache on. Lower it with
`advanced.analysis.max_refinement_passes` or `--refinement-passes`; `preset: fast` uses 3.

If the review is still dropping clips when the budget runs out, one final review runs that drops
without refilling — a cut four seconds short beats a cut that ends on a photo of a shelf.

See [Pipeline overview](./pipeline-overview.md) for how this sits in the run as a whole.

The Step 2 checkboxes define the source pool. **Fast** means “deeply analyze fewer videos,” not
“throw the rest away.” The completion summary reports eligible media, videos deeply analyzed, and
clips finally planned as separate numbers.

### What counts as one moment

Two shots are the same moment when they are close enough together in a memory of
this length. Five minutes is a moment inside a sixty-second month, where the cut has
a slot for most of the days in it. Across a year it is a rounding error: two clips of
one evening, an hour apart, are one evening to anybody watching, and a rendered year
recap spent two of its thirty-nine slots on a single night at a venue.

| memory spans | one moment is |
|---|---|
| up to a month | 5 minutes |
| up to a season | 30 minutes |
| up to a year | 90 minutes |
| longer | 3 hours |

That five minutes is `temporal_dedup_window_minutes`, and it is a floor rather than a
ceiling: the wider spans below it always win. It is not a dial you can reach, though. The
field lives on `PipelineConfig` in `analysis/smart_pipeline.py` with no YAML key and no CLI
flag wired to it, so five minutes is what every run gets.

A moment keeps one clip unless moments are genuinely scarce: only when there are at
most half as many as the cut needs clips does a moment contribute more than one, which
is the case a five-minute trip memory hits and a month recap does not.

### Sparse Content Adaptations

When content is limited, the pipeline adapts automatically:

- **Media-aware trip Auto duration**: The editorial curve is 30 seconds plus 10 seconds per active day, bounded to 60–300 seconds for dense trips. Usable video excerpts, at most four photos per day for capacity estimation, and a 30-second/day diversity ceiling can lower it. Sparse trips may resolve below 60 seconds.
- **Auto LLM budget**: Auto runs LLM analysis for every eligible clip when at most 60 clips need fresh analysis. For larger libraries it uses a time-balanced shortlist. Compatible current-model cache hits do not consume this budget.
- **Temporal coverage**: Every time period gets at least one clip. Sole monthly representatives are protected from removal during duration scaling, even if they score lower than favorites in other months.
- **Progressive backfill**: The selector first uses strict preferences, then allows up to 70% photos, additional non-favorites, closer moments, and finally any eligible photo ratio. Conceding on closeness gives back the width a long memory added, never the rule itself — the concession is a clip from an evening already in the cut, never the same shot twice. If the only remaining clip is slightly too long, it may accept up to two seconds of overrun for the renderer to trim.
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

### How much of the pool was actually looked at

Not every candidate gets analyzed. The ones that don't are scored from metadata — duration,
resolution, whether you starred it — and metadata produces a lot of identical scores. On a real
April 2021 recap, 25 of 149 candidates had been visually analyzed and 55% of the pool carried the
same fallback score. When scores tie, the ranking is list order wearing a number.

So the pipeline now says so. When fewer than 60% of the candidates were visually analyzed, you get
one line — in the review step and in `generate` output:

> 25 of 149 candidates (17%) were visually analyzed; the rest were picked on metadata. Review
> recommended.

Above 60% it says nothing, because a warning on every run is a warning nobody reads on the run that
needed it. The count is always in `--trace-selection` output, thin or not.

Treat it as a signal about where your attention is worth spending, not an error. A low number
usually means an uncurated period with no favorites to seed from and a pool the analysis budget
never reached. The clips are fine; the *ranking* between them is close to arbitrary, so the review
step is doing more work than usual. Run `--analysis-depth thorough` if you'd rather the machine
decide, or just spend the extra minute in review.

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
