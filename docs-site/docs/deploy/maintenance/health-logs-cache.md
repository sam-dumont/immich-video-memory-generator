---
sidebar_label: "Health, Logs & Cache"
---

# Health, Logs & Cache

Three operational aspects you'll want to understand for any deployment beyond "run it once and forget."

## Health endpoints

Use `GET /health/live` for liveness and `GET /health/ready` for readiness. Liveness always returns
`200` while the web process can answer, with `{"status": "alive", "version": "..."}`. It does not
contact Immich.

Readiness checks configuration and Immich. Its payload is `status: ready` with HTTP `200` when both
are usable, or `status: degraded` with HTTP `503` when configuration is missing or Immich cannot
be reached. `GET /health` always returns HTTP `200` for compatibility; it rewrites a ready payload
to `ok` and leaves a degraded payload as `degraded`. Do not use `/health` as a readiness probe.

`/health/ready` returns JSON with the current system status:

```json
{
  "status": "ready",
  "immich_reachable": true,
  "last_successful_run": "2025-12-15T10:30:00.000000",
  "version": "0.40.1"
}
```

| Field | Values | Meaning |
|-------|--------|---------|
| `status` | `ready` / `degraded` | `ready` only when configuration and authenticated Immich access work; otherwise `degraded` |
| `immich_reachable` | `true` / `false` | Whether the dependency probe reached Immich; authentication or version failures can still make readiness fail |
| `last_successful_run` | ISO timestamp or `null` | Last completed video generation, from the run database |
| `version` | semver string | Installed version of Immich Memories |

The readiness check probes Immich and authenticates the current user, bounded by 5 seconds. If
Immich is down, the status flips to `degraded` and readiness returns `503`, but the application
keeps running (you can still browse the UI, review cached clips, etc.).

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

### Log file

Set `IMMICH_MEMORIES_LOG_FILE=/path/to/immich-memories.log` to write the same lines to a file in
addition to stdout (same format as chosen above). In Docker, point it at a mounted path.

### Log level

`INFO`. There is no user-facing switch for the log level yet — no env var, no CLI flag. If you
need `DEBUG` output for a bug report, run from a checkout and call
`configure_logging(level="DEBUG")` in code.

## Video cache

Downloaded Immich clips are cached locally to avoid re-downloading on repeat runs. The cache lives at `~/.immich-memories/cache/video-cache/` (or the path set in `cache.directory` config).

### How it works

The cache uses a two-level directory structure: `{id[:2]}/{id}{ext}`. When you request a clip, it checks the cache first. On a hit, it runs a quick `ffprobe` on the file and returns the local path; if ffprobe cannot read it (a truncated or corrupt file), the entry is deleted and downloaded again. On a miss, it streams the download into `{id}{ext}.part` and renames it into place only once complete, so a run killed mid-download never leaves a half file that the next run would trust. Leftover `.part` files nobody has written to for an hour are removed at the start of the next run.

### Eviction

Two eviction strategies run automatically:

1. **Age-based eviction**: removes files older than `video_cache_max_age_days` (default: 7 days). Runs at the start of every generation.
2. **Size-based eviction**: removes oldest files (by modification time, LRU) until the cache is under `video_cache_max_size_gb` (default: 10 GB). Runs after each download during a run — files the current run already handed out are spared until it finishes, so a large prefetch can temporarily exceed the cap — and once more at the end of the run.

### Configuration

```yaml
cache:
  directory: ~/.immich-memories/cache
  database: ~/.immich-memories/cache.db
  max_age_days: 30                  # Analysis cache age (not video cache)
  video_cache_enabled: true
  video_cache_max_size_gb: 10.0     # Max disk usage for downloaded videos
  video_cache_max_age_days: 7       # Evict videos older than this
  thumbnail_cache_max_size_mb: 500  # Max disk for Immich thumbnails
  preview_cache_max_size_mb: 2000   # Max disk for clip previews
```

### Thumbnails and previews

Thumbnails (`thumbnails/`) and clip previews (`preview-cache/`, `previews/`) are
derived from your library rather than downloaded from it, so they are cheap to
rebuild and get smaller budgets than the video cache. Each is evicted
least-recently-used once it goes over its limit.

Before these limits existed neither directory had a cap or an expiry, so both
grew for as long as the app ran — on one real library, 5.2 GB of previews and
3.5 GB of thumbnails.

The `max_age_days` at the top level controls the analysis database cache (SQLite), not the video file cache. The `video_cache_*` fields control the file-based video cache.

### Cache stats and management

From the CLI:

```bash
# View cache stats
immich-memories cache stats
```

The CLI has no `clear` command. To clear caches:

- **UI**: the Cache page (sidebar > Cache) shows current usage and has per-cache
  **Clear** buttons plus a **Clear all**.
- **Shell**: the video and thumbnail caches are plain directories that are safe to
  delete while the app is idle:

  ```bash
  rm -rf ~/.immich-memories/cache/video-cache      # downloaded clips (re-downloaded on demand)
  rm -rf ~/.immich-memories/cache/thumbnails       # UI thumbnails
  # Docker: docker exec immich-memories rm -rf /home/immich/.immich-memories/cache/video-cache
  ```

  The analysis cache lives in `~/.immich-memories/cache.db`; deleting it forces a full
  re-analysis on the next run, so back it up first (`immich-memories cache backup`).

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
