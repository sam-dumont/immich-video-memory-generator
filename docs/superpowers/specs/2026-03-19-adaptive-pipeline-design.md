# Adaptive Pipeline: Density Budget, Cache-First Scoring, Live Photo Unification

**Issues**: #67 (prefiltering), #68 (live photo unification), #69 (temp cleanup)
**Date**: 2026-03-19
**Status**: Approved design, pending implementation

## Problem

The smart pipeline's Phase 2 filter aggressively removes non-favorites before LLM analysis. With the resolution filter bug fixed (PR #70 — all non-favorites had 0×0 dimensions), the filter works correctly but the selection strategy is still too rigid: favorites-only analysis misses great moments in non-favorite clips, and there's no density awareness across the timeline.

Additionally, live photos enter the pipeline unscored, and temp files aren't cleaned up after generation.

## Root Cause Data (Emile 2025)

```
6352 total assets: 604 videos, 5089 photos, 659 live photos
862 favorites → 5081s of raw footage (461% of 1101s budget)
0 non-favorites survived old filter (all had 0×0 resolution)
After PR #70 fix: 985/1100 non-fav videos survive
```

Favorites alone massively overflow the budget. The challenge is selecting the BEST favorites + filling temporal gaps, not finding enough content.

## Design

### 1. Density-Proportional Time Budget

Each time bucket (month for year-long, week for shorter periods) gets a raw footage quota proportional to its share of ALL assets (videos + photos + live photos).

```
Target video:     10 minutes = 600s
Title overhead:   ~50s (title 3.5s + ending 7s + transitions + month dividers)
Content budget:   550s
Raw budget:       550s × 2.0 = 1100s (clips get trimmed to best segments)

Bucket quota = raw_budget × (bucket_asset_count / total_asset_count)

Example:
  August (1200 assets, 7.3%): quota = 1100 × 0.073 = 80s
  February (300 assets, 1.8%): quota = 1100 × 0.018 = 20s
```

Dense months (summer, holidays, birthdays) naturally get more representation.

### 2. Cache-First Analysis

LLM analysis is expensive (~8s per asset on local VLM) but results are stable — the same photo always gets the same score. Analyze once, cache forever.

**New `asset_scores` table** (extends existing `VideoAnalysisCache` SQLite DB):

```sql
CREATE TABLE IF NOT EXISTS asset_scores (
    asset_id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL,          -- 'video', 'photo', 'live_photo'
    llm_interest REAL,                 -- 0.0-1.0
    llm_quality REAL,                  -- 0.0-1.0
    llm_emotion TEXT,                  -- 'joy', 'calm', 'excited', etc.
    llm_description TEXT,              -- brief description
    metadata_score REAL NOT NULL,      -- faces, favorites, EXIF, resolution
    combined_score REAL NOT NULL,      -- final blended score
    analyzed_at TEXT NOT NULL,         -- ISO timestamp
    model_version TEXT                 -- LLM model that produced the score
);
```

**Generation flow:**

```
1. Fetch all assets (videos + photos + live photos)
2. Cache lookup: partition into cached vs uncached
3. For uncached assets:
   - Fast mode: metadata score only (no download, no LLM)
   - Thorough mode: download → LLM → cache result
4. Apply density budget using scores (cached or freshly computed)
5. Select clips per bucket (favorites first, gap-fill if needed)
6. Render selected clips → assemble → output
```

**First run**: expensive (all favorites get LLM'd, results cached). ~2h for 862 favorites.
**Subsequent runs**: near-instant (cache hits). Only new assets since last run get analyzed.
**Background mode** (future): generate memory NOW with metadata scoring, queue uncached assets for background LLM analysis. Next generation benefits from the cache.

### 3. Analysis Depth Modes

| Mode | Favorites | Gap-fillers | LLM calls | Use case |
|------|-----------|-------------|-----------|----------|
| **Fast** (default) | Cache hit or metadata-only | Metadata-only | 0 (warm cache) or N (cold) | Daily use, quick regeneration |
| **Thorough** | Cache hit or full LLM | Top-3 shortlist per bucket → LLM | N + ~3×gaps | Best quality, first run |
| **Analyze All** | Full LLM on everything | Full LLM on everything | All assets | GUI override, full manual control |

Scene detection (FFmpeg, ~2s) always runs on selected clips regardless of mode — it's needed for proper segment extraction.

**Favorite capping**: When a bucket has more favorites than its quota × 1.5, only analyze the top N by metadata score. No point LLM-scoring 77 January favorites when the quota is 24.

### 4. Live Photo Unification (#68)

Live photos already enter the pipeline as `VideoClipInfo` objects (appended after burst merging). Changes needed:

1. **Favorite inheritance**: If ANY member of a burst cluster is `is_favorite`, the merged clip is marked favorite. Currently the merged clip loses the favorite flag.

2. **Scoring**: Live photo clips go through the same scoring pipeline as videos. Apply a `live_photo_penalty = 0.9×` (configurable) since live photos are less intentional than deliberate recordings.

3. **Filter bypass**: Merged live photo clips have no EXIF make/model (they're synthetic). The compilation filter must not drop them. Check: `is_live_photo_merge OR has_exif`.

4. **Scene detection skip**: Live photo clips are 3s — no scene boundaries to detect. Skip scene detection, use the full clip as a single segment.

### 5. Temp Cleanup (#69)

After assembly completes, delete intermediate directories:

```python
def _cleanup_temp_dirs(output_dir: Path) -> None:
    for subdir in (".title_screens", "photos"):
        path = output_dir / subdir
        if path.exists():
            shutil.rmtree(path)
```

Called in `generate_memory()` after `_cleanup_temp_clips()`. The `--keep-intermediates` flag skips this cleanup (already exists for `.assembly_temps`).

### 6. Cache Persistence & Backup

The cache DB (`~/.immich-memories/cache.db`) now stores LLM analysis scores — worth protecting.

**Docker:**
```yaml
volumes:
  - ./data:/data
environment:
  - IMMICH_MEMORIES_CACHE__DIRECTORY=/data/cache
  - IMMICH_MEMORIES_CACHE__DATABASE=/data/cache.db
```

**Kubernetes:** PVC mounted at `/data`, cache.db inside. Survives pod restarts.

**Safe SQLite backup**: SQLite files can't be naively copied while the DB is in use — a partial write during copy produces a corrupted backup. The `cache export` command uses SQLite's built-in backup API (`sqlite3.Connection.backup()`) which handles locking correctly:

```python
# Safe backup: uses SQLite's online backup API
import sqlite3
src = sqlite3.connect("cache.db")
dst = sqlite3.connect("backup.db")
src.backup(dst)  # Atomic, lock-safe, works during active writes
```

For Docker volume snapshots or filesystem-level backups, the safest approach is to run `cache export` first, or ensure no active generation is running during the backup.

**CLI backup commands** (new):
```bash
immich-memories cache export scores.json    # Safe JSON export (uses SQLite backup API)
immich-memories cache import scores.json    # Restore from JSON backup
immich-memories cache stats                 # Show cache hit rate, size, age, last analysis
immich-memories cache backup cache.db.bak   # Direct DB backup via SQLite backup API
```

**WAL mode**: The cache DB should use `PRAGMA journal_mode=WAL` for concurrent read/write safety. WAL allows readers during writes and is the recommended mode for application databases. Already used by the existing `VideoAnalysisCache`.

**Documentation updates**: Add cache persistence notes to Docker, K8s, and Terraform install docs. Note that cache.db contains analysis results and should be backed up. Include warning about not copying the `.db` file directly while the app is running — use `cache export` or `cache backup` instead.

### 7. Config & CLI Changes

**Config** (`config.yaml`):
```yaml
# Tier 1
photos:
  enabled: true                    # Default true (was false)

# Tier 2 (advanced)
analysis:
  analysis_depth: fast             # fast | thorough
  live_photo_penalty: 0.9          # Score multiplier for live photo clips
  favorite_quota_buffer: 1.5       # Analyze up to 1.5× quota of favorites per bucket
```

**CLI**:
```bash
--analysis-depth fast|thorough     # Override analysis depth
# --include-photos no longer needed (photos on by default)
# Existing: --include-live-photos stays for backward compat
```

**GUI**: "Analyze all" button continues to work as `analyze_all=True` override.

## Implementation Order

1. **#69 Temp cleanup** — 10 min, standalone
2. **Cache schema** — add `asset_scores` table with migration
3. **Density budget calculator** — replace `_phase_filter()` logic
4. **Cache-first scoring** — lookup/store in `asset_scores`
5. **Live photo favorite inheritance** — in burst merge pipeline
6. **Live photo filter bypass** — skip compilation filter for merged clips
7. **CLI/config** — `--analysis-depth`, `photos.enabled=true` default
8. **Cache CLI** — `cache export/import/stats` commands
9. **Documentation** — update Docker/K8s/Terraform docs for cache persistence

## Not In Scope

- Background analysis queue (future — generate now, analyze later)
- Photo-specific LLM prompt tuning (current prompt works for both)
- Playlist/chapter support for long videos
- Cache sharing between multiple Immich instances
