---
sidebar_label: "Health, Logs & Cache"
---

# Health, Logs & Cache

## Health endpoints

Use `GET /health/live` for liveness and `GET /health/ready` for readiness. Liveness always returns
`200` while the web process can answer, with `{"status": "alive", "version": "..."}`. It does not
contact Immich.

Readiness checks configuration and Immich. Its payload is `status: ready` with HTTP `200` when both
are usable, or `status: degraded` with HTTP `503` when configuration is missing or Immich cannot
be reached. `GET /health` always returns HTTP `200` for compatibility; it rewrites a ready payload
to `ok` and leaves a degraded payload as `degraded`. Do not use `/health` as a readiness probe.

`/health/ready` returns JSON with the current system status (abridged — the real payload also
carries automation, pending-delivery, scheduler and Immich blocks):

```json
{
  "status": "ready",
  "immich_reachable": true,
  "last_successful_run": "2025-12-15T10:30:00.000000",
  "version": "0.59.2"
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
  thumbnail_cache_max_size_mb: 10000  # Max disk for Immich previews
  preview_cache_max_size_mb: 2000     # Max disk for clip previews
```

### Thumbnails: the budget that scales with your library

`thumbnails/` holds one Immich preview per candidate asset a memory's scope can
reach — not per clip in the finished cut. Generating a memory reads each of
those previews back several times: sharpness and exposure, the DINOv2 heads, the
contact sheets, the caption. Measured on a real library, a preview is about
**315 KB**, so a 10,793-candidate scope wants roughly 3.4 GB and a real cache
held 12,159 previews for 3.92 GB.

Size it by your library:

```
thumbnail_cache_max_size_mb ≈ 0.35 × (assets a memory's scope can reach)
```

The 10 GB default holds about 31,000 previews. Previews this run is still using
are never evicted, so a run whose working set does not fit overflows the limit
rather than losing facts halfway through. What you pay instead is on the *next*
run, which reclaims them: the next overlapping memory re-downloads every preview
and re-captions the assets whose banked caption failure no longer matches the
bytes it was recorded against. That is model work, not just bandwidth, which is
why one `WARNING` per run says how far over you are and names the setting.

Clip previews (`preview-cache/`, `previews/`) are different: their working set is
one cut's clips — tens of files per run however big your library is — so 2 GB
stays a plain cap and needs no rule of thumb. The video cache is the same shape.

Before these limits existed neither directory had a cap or an expiry, so both
grew for as long as the app ran — on one real library, 5.2 GB of clip previews
and 3.5 GB of thumbnails.

The `max_age_days` at the top level controls the analysis database cache (SQLite), not the video file cache. The `video_cache_*` fields control the file-based video cache.

### Cache stats and management

From the CLI:

```bash
# View cache stats
immich-memories cache stats
```

`cache stats` reports the legacy photo scorer's table, which nothing writes any
more; the editor's facts and banks live in `annotations.sqlite` beside it (see
[Editorial annotation setup](../configuration/editorial-preparation.md)).

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

  `~/.immich-memories/cache.db` holds the run history and automation state, so back it up
  before deleting it (`immich-memories cache backup`). The editor's banks are in
  `annotations.sqlite` in the same directory; deleting that re-asks the model everything.

### Analysis database

Separate from the video cache. `cache.db` holds the run history, the automation state and the
tables the legacy scorer used to fill. What the editor learned about your library — captions,
head facts, readings and banked answers — is in `annotations.sqlite`, which persists across video
cache evictions. You can safely clear the video cache without losing any of it.

### Disk space planning

| Content | Storage needed |
|---------|---------------|
| Video cache (30 clips, 1080p) | ~3-5 GB |
| Video cache (100 clips, 4K) | ~15-25 GB |
| Analysis database (1000 videos) | ~50 MB |
| Generated output (30 clips, 1080p) | ~500 MB per video |

For NAS users: set `video_cache_max_size_gb` to something your disk can handle. The default 10 GB is reasonable for most setups.
