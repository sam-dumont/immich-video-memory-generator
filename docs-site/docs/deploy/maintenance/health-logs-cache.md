---
sidebar_label: "Health, Logs & Cache"
---

# Health, Logs & Cache

Three operational aspects you'll want to understand for any deployment beyond "run it once and forget."

## Health endpoint

`GET /health` returns JSON with the current system status:

```json
{
  "status": "ok",
  "immich_reachable": true,
  "last_successful_run": "2025-12-15T10:30:00.000000",
  "version": "0.2.0"
}
```

| Field | Values | Meaning |
|-------|--------|---------|
| `status` | `ok` / `degraded` | `ok` when Immich is reachable, `degraded` when it's not |
| `immich_reachable` | `true` / `false` | Result of pinging Immich's `/api/server/ping` endpoint |
| `last_successful_run` | ISO timestamp or `null` | Last completed video generation, from the run database |
| `version` | semver string | Installed version of Immich Memories |

The health check pings Immich with a 5-second timeout. If Immich is down, the status flips to `degraded` but the application keeps running (you can still browse the UI, review cached clips, etc.).

Use this endpoint with monitoring tools: Uptime Kuma, Prometheus blackbox exporter, or a simple `curl` in a cron job.

## Logging

Two output formats, controlled by the `IMMICH_MEMORIES_LOG_FORMAT` environment variable:

### Text format (default)

```
2025-12-15 10:30:00,123 [INFO] immich_memories.generate [abc123]: Assembling final video...
```

Format: `timestamp [LEVEL] logger_name [run_id]: message`

The `run_id` field (the `abc123` part) correlates all log lines from a single pipeline run. When no pipeline is active, it shows `-`.

### JSON format

Set `IMMICH_MEMORIES_LOG_FORMAT=json` for structured output:

```json
{
  "timestamp": "2025-12-15T10:30:00.123456+00:00",
  "level": "INFO",
  "logger": "immich_memories.generate",
  "run_id": "abc123",
  "message": "Assembling final video..."
}
```

The `run_id` field only appears when a pipeline run is active. Filter in production with: `jq 'select(.run_id=="abc123")'`.

### Log level

Set via the standard Python logging level: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`. Configure by calling `configure_logging(level="DEBUG")` or setting it in code.

## Video cache

Downloaded Immich clips are cached locally to avoid re-downloading on repeat runs. The cache lives at `~/.immich-memories/cache/video-cache/` (or the path set in `cache.directory` config).

### How it works

The cache uses a two-level directory structure: `{id[:2]}/{id}{ext}`. When you request a clip, it checks the cache first. On a hit, it returns the local path instantly. On a miss, it downloads from Immich and stores the file.

### Eviction

Two eviction strategies run automatically:

1. **Age-based eviction** (`evict_old`): removes files older than `video_cache_max_age_days` (default: 7 days). Runs at the start of every generation.
2. **Size-based eviction** (`evict_if_over_limit`): removes oldest files (by modification time, LRU) until the cache is under `video_cache_max_size_gb` (default: 10 GB). Runs after each new download.

### Configuration

```yaml
cache:
  directory: ~/.immich-memories/cache
  database: ~/.immich-memories/cache.db
  max_age_days: 30                  # Analysis cache age (not video cache)
  video_cache_enabled: true
  video_cache_max_size_gb: 10.0     # Max disk usage for downloaded videos
  video_cache_max_age_days: 7       # Evict videos older than this
```

The `max_age_days` at the top level controls the analysis database cache (SQLite), not the video file cache. The `video_cache_*` fields control the file-based video cache.

### Cache stats and management

From the CLI:

```bash
# View cache stats
immich-memories cache stats

# Clear video cache (analysis cache is preserved)
immich-memories cache clear-videos

# Clear everything
immich-memories cache clear
```

From the UI: the Cache management page (sidebar > Cache) shows current usage and has clear buttons.

### Analysis cache

Separate from the video cache. Analysis scores, face detections, and LLM content results are stored in a SQLite database (`cache.db`). This is the most valuable cache: re-analyzing a library of 500 videos takes 20+ minutes, but cache hits are instant.

The analysis cache persists across video cache evictions. You can safely clear the video cache without losing analysis results.

### Disk space planning

| Content | Storage needed |
|---------|---------------|
| Video cache (30 clips, 1080p) | ~3-5 GB |
| Video cache (100 clips, 4K) | ~15-25 GB |
| Analysis database (1000 videos) | ~50 MB |
| Generated output (30 clips, 1080p) | ~500 MB per video |

For NAS users: set `video_cache_max_size_gb` to something your disk can handle. The default 10 GB is reasonable for most setups.
