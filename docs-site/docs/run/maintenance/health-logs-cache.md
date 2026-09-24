---
sidebar_label: "Health, logs and caches"
---

# Health, logs and caches

Reader: power user.

## Health endpoints

| Endpoint | Returns | Use it for |
|---|---|---|
| `GET /health/live` | `200` while the web process answers, `{"status": "alive", "version": …}`. Never contacts Immich | liveness probe |
| `GET /health/ready` | `200` with `status: ready` when configuration and authenticated Immich access work; `503` with `status: degraded` otherwise | readiness probe, Uptime Kuma, blackbox exporter |
| `GET /health` | always `200`: a ready payload is rewritten to `ok`, a degraded one passes through as `degraded` | compatibility only, never a probe |

Because `GET /health` always returns HTTP `200` and rewrites a ready payload to `ok`, it is
useless as a probe.

All three are unauthenticated, on purpose: a container runtime has no session. The Immich check is
bounded at 5 seconds, and the answer is reused for up to 10 seconds so a busy poller doesn't hammer
Immich. With login on, only a logged-in session sees the automation and run detail (it carries
person names and paths); a probe gets the status and the version. A degraded status never stops
the app: the UI still serves.

```json
{"status": "ready", "immich_reachable": true, "last_successful_run": "2025-12-15T10:30:00", "version": "0.77.2"}
```

## Logging

`INFO` by default. `immich-memories -v generate …` logs at `DEBUG`; `--log-level WARNING` keeps
warnings and errors. Both are root options, so they go before the subcommand and work for `ui` too.
In a container, set `IMMICH_MEMORIES_LOG_LEVEL=DEBUG`. `generate --quiet` and `auto run --quiet`
change what the terminal shows, not what is logged.

Lines look like `2025-12-15 10:30:00,123 [INFO] immich_memories.generate [abc123]: Assembling final
video...`: the bracketed run id ties one run's lines together (`-` outside a run).
`IMMICH_MEMORIES_LOG_FORMAT=json` writes one JSON object per line with the same fields, so
`jq 'select(.run_id=="abc123")'` works. `IMMICH_MEMORIES_LOG_FILE=/path/to/file.log` writes the
same lines to a file as well; in Docker, point it at a mounted path.

## Model usage records

Only with a model. Each selection attempt keeps `llm-usage.json` under
`cache/editorial-runs/<memory>/attempts/<attempt>/`: calls, cache hits and tokens, split
`by_stage` (`caption_controls`, `caption`, `motion`, `reader`) and `by_model`. It is checkpointed as
the run goes, so a killed run leaves its last count. When a server returns no token counts,
`unmetered_calls` counts those calls and `usage_complete` is `false`: the totals are then a floor.
Provider batch lines count the same way, with `batch_unmetered_calls` and `batch_usage_complete`.

## Caches

Everything lives under `~/.immich-memories/cache/` (or `cache.directory`):

| Directory or file | What it holds | Cap |
|---|---|---|
| `annotations.sqlite` | every fact the app banked: head answers, detector verdicts, measurements, and captions and readings when a model is used, each keyed by producer and exact input | none; this is the file to keep |
| `thumbnails/` | one Immich preview per candidate a film can reach | `thumbnail_cache_max_size_mb`, 10 GB |
| `video-cache/` | downloaded Immich clips | `video_cache_max_size_gb` 10 GB, `video_cache_max_age_days` 7 |
| `preview-cache/`, `previews/` | clip previews for the web UI | `preview_cache_max_size_mb`, 2 GB |
| `../cache.db` (one level up) | run history and automation state | none |

```yaml
cache:
  directory: ~/.immich-memories/cache
  database: ~/.immich-memories/cache.db
  video_cache_enabled: true
  video_cache_max_size_gb: 10.0
  video_cache_max_age_days: 7
  thumbnail_cache_max_size_mb: 10000
  preview_cache_max_size_mb: 2000
```

### What a second cut asks again

Nothing in `annotations.sqlite` is keyed to a run, so a second cut over the same pictures reuses
every fact the first one banked. Standing is read from each picture's facts and asks nothing at all.
With a model, the period reading is banked one calendar month at a time, so a monthly cut after a
yearly one asks nothing again for that month. A warm cut spends its time on video work. When a
release changes a prompt, the answers that prompt produced are asked again once; captions, head
answers and detector verdicts are keyed by their own producers and stay warm.

### The facts a cut measures

Three facts are measured only once a cut has chosen a picture, and banked in `annotations.sqlite`:

| Table | What it holds | Written when |
|---|---|---|
| `motion_residuals` | the optical flow of one Live Photo's companion video, including the residual the 1.5 threshold reads | a cut measures a chosen Live Photo |
| `speech_regions` | the utterances a clip holds, in its own seconds; an empty list means "listened, heard none" | a cut keeps a video or a playing Live Photo |
| `live_clock_offsets` | how the clocks of a Live burst's companion videos line up; an empty answer means the burst ships as its photograph | a cut keeps a burst of two or more Live Photos |

Each row is keyed by the picture, its source metadata, and a producer version that includes the
method and, for speech, `speech.vad_threshold` and `speech.min_silence_ms`. Change any of those and
the next cut measures again. The next cut reads these before it plans: a Live Photo whose banked
residual is under 1.5 is planned as a still from the start. Only pictures a cut reaches are
measured, and every later cut reads them for free.

### The preview cache scales with your library

A run reads each candidate's preview several times. One preview is about 315 KB, so size the cache
as `thumbnail_cache_max_size_mb ≈ 0.35 × pictures a film can reach`; the 10 GB default holds about
31,000. Previews the current run uses are never evicted, so a run that doesn't fit overflows the
cap rather than losing facts, and one `WARNING` says how far over you are. The next overlapping
run pays for it by downloading those previews again.

### Video cache mechanics

Files sit at `{id[:2]}/{id}{ext}`. A hit is `ffprobe`d first and fetched again if unreadable. A
download streams into `{id}{ext}.part` and is renamed only when complete, so a killed run leaves
nothing the next one would trust; `.part` files idle for an hour go at the next start. Age eviction
runs at the start of every run, size eviction after each download and once at the end.

### Clearing

The Cache page in the web UI shows usage, with a **Clear** button per cache and **Clear all**. From
a shell, the video and thumbnail caches are plain directories, safe to delete while the app is
idle:

```bash
rm -rf ~/.immich-memories/cache/video-cache
rm -rf ~/.immich-memories/cache/thumbnails
```

Don't point `rm -rf` at `~/.immich-memories/cache` itself: `annotations.sqlite` is inside, and
without it every fact about your library is prepared again.

### The CLI cache commands are not for the banks

`immich-memories cache stats|backup|export|import` read and write `asset_scores`, the retired
per-clip scorer's table, which nothing writes any more. They don't touch `annotations.sqlite`. To
move an install, copy `~/.immich-memories` (in Docker: the config volume).
