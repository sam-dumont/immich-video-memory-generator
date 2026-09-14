---
sidebar_label: "Health, Logs & Cache"
---

# Health, Logs & Cache

## Health endpoints

| Endpoint | Returns | Use it for |
|---|---|---|
| `GET /health/live` | `200` while the web process answers, `{"status": "alive", "version": …}`. Never contacts Immich | liveness probe |
| `GET /health/ready` | `200` with `status: ready` when configuration and authenticated Immich access work; `503` with `status: degraded` otherwise | readiness probe, Uptime Kuma, blackbox exporter |
| `GET /health` | always `200`; a ready payload is rewritten to `ok` | compatibility only, not a probe |

`GET /health` always returns HTTP `200` for compatibility and rewrites a ready payload to `ok`; a
degraded one is passed through as `degraded` with the same `200`, which is what makes it useless as
a probe.

All three are unauthenticated, on purpose and even with login turned on: a container runtime has no
session. They carry the version, whether config is present and whether Immich answered, and nothing
about your library. Put them behind your ingress rules if that is more than you want to publish.

An abridged readiness payload:

```json
{"status": "ready", "immich_reachable": true, "last_successful_run": "2025-12-15T10:30:00", "version": "0.77.2"}
```

The readiness payload also carries `immich_reachable`, `last_successful_run` (from the run
database), `version`, and the automation, pending-delivery and scheduler blocks. The Immich probe
is bounded at 5 seconds. A degraded status does not stop the app: the UI still serves.

## Logging

`INFO` by default. `immich-memories -v generate …` logs at `DEBUG`; `--log-level WARNING` keeps
warnings and errors. Both are root options, so they go before the subcommand and apply to `ui` as
well. In a container, `IMMICH_MEMORIES_LOG_LEVEL=DEBUG`. `generate --quiet` and `auto run --quiet`
are a different knob: they change what the terminal shows, not what is logged.

Lines look like `2025-12-15 10:30:00,123 [INFO] immich_memories.generate [abc123]: Assembling final
video...`; the bracketed run id ties every line of one run together (`-` outside a run).
`IMMICH_MEMORIES_LOG_FORMAT=json` switches to one JSON object per line with the same fields, so
`jq 'select(.run_id=="abc123")'` works. `IMMICH_MEMORIES_LOG_FILE=/path/to/file.log` writes the
same lines to a file as well as stdout; in Docker, point it at a mounted path.

## Caches

Everything lives under `~/.immich-memories/cache/` (or `cache.directory`):

| Directory or file | What it holds | Cap |
|---|---|---|
| `annotations.sqlite` | every caption, head answer, detector verdict and reading the editor banked, keyed by producer and exact input | none; this is the file to keep |
| `thumbnails/` | one Immich preview per candidate a memory's scope can reach | `thumbnail_cache_max_size_mb`, 10 GB |
| `video-cache/` | downloaded Immich clips | `video_cache_max_size_gb` 10 GB, `video_cache_max_age_days` 7 |
| `preview-cache/`, `previews/` | clip previews for the web UI | `preview_cache_max_size_mb`, 2 GB |
| `../cache.db` (one level up) | run history, automation state, and the retired scorer's table | none |

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

`cache.max_age_days` is still accepted and nothing reads it.

### The preview cache scales with your library

Generating a memory reads each candidate's preview several times (sharpness, the heads, the
contact sheets, the caption). One preview is about 315 KB, so size it as
`thumbnail_cache_max_size_mb ≈ 0.35 × pictures a memory's scope can reach`; the 10 GB default holds
about 31,000. Previews the current run uses are never evicted, so a run that does not fit overflows
the cap rather than losing facts. The price lands on the next overlapping run, which re-downloads
every preview and re-captions the pictures whose banked caption failure no longer matches the
bytes it was recorded against. One `WARNING` per run says how far over you are and names the
setting. The clip and video caches hold one cut's worth of files however big the library is, so
their caps are plain caps.

### Video cache mechanics

Files sit at `{id[:2]}/{id}{ext}`. A hit is `ffprobe`d first; an unreadable file is deleted and
fetched again. A download streams into `{id}{ext}.part` and is renamed into place only when
complete, so a run killed mid-download leaves nothing the next run would trust; `.part` files idle
for an hour are removed at the next start. Age eviction runs at the start of every run; size
eviction runs after each download (sparing files the run already handed out) and once more at the
end with nothing spared.

### Clearing

The UI's Cache page (sidebar, Cache) shows usage and has per-cache **Clear** buttons and **Clear
all**. From a shell, the video and thumbnail caches are plain directories, safe to delete while the
app is idle:

```bash
rm -rf ~/.immich-memories/cache/video-cache
rm -rf ~/.immich-memories/cache/thumbnails
```

Do not point `rm -rf` at `~/.immich-memories/cache` itself: `annotations.sqlite` is inside it, and
deleting it re-asks the model everything about your library.

### The CLI cache commands are not for the banks

`immich-memories cache stats|backup|export|import` read and write `asset_scores`, the retired
per-clip scorer's table, which nothing writes any more. They do not touch `annotations.sqlite`.
To move an installation, copy `~/.immich-memories` (Docker: the config volume).

`cache.db` has a versioned schema migrator that runs when it is first opened. `annotations.sqlite`
creates tables when missing and adds columns additively. Neither runs at process start.
