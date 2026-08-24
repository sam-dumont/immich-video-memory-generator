---
sidebar_position: 1
title: Docker
---

# Install with Docker

No Python environment to manage. Pull the image, set two env vars, done.

## Quick start

Create a `.env` file next to your `docker-compose.yml`:

```bash
IMMICH_URL=https://photos.example.com
IMMICH_API_KEY=your-api-key-here
```

Get the API key from Immich: **Account Settings > API Keys > New API Key**. When Immich asks which permissions to grant, pick **All** — or, for a minimal key: read access to assets, people, albums, timeline and search, plus **asset upload** and **album create/update** if you turn on upload-back to Immich. This tool never deletes or modifies existing assets.

Then grab the compose file from the repo and start it:

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
docker compose up -d
```

UI is at [http://localhost:8080](http://localhost:8080).

:::caution Do not publish the default UI
Authentication is disabled by default, and the container listens on `0.0.0.0`. Anyone who can
reach port 8080 can use the app. Enable [authentication](../configuration/authentication) before
exposing it. The UI is single-user, single-replica; keep this service at one instance.
:::

The compose volume at `/home/immich/.immich-memories` must stay writable. It holds config, cache,
automation history, and pending-delivery state.

:::caution Who owns ./output
The image writes to `/app/output` (the Dockerfile sets `IMMICH_MEMORIES_OUTPUT__DIRECTORY`) and the
compose file mounts `./output` there, so videos land on your host without extra configuration. That
is an environment variable, so it beats `output.directory` in `config.yaml` — to write somewhere
else, override the variable in the service's `environment:` block, not in the YAML.

What you do have to get right is ownership. The container runs as the unprivileged `immich` user, **UID/GID 1000** — the first user on most
Linux hosts, so a `./output` folder you create yourself is writable without any `chown`. If Docker
creates the folder for you it is owned by root; then either `mkdir -p output` before the first
`up`, or `sudo chown 1000:1000 output`. On a host where your user is not 1000, set
`user: "<uid>:<gid>"` on the service — the config volume must then be writable by that UID too
(bind-mount it and `chown` it the same way) — or use a named volume
(`immich-memories-output:/app/output`) and copy files out with `docker cp`.
:::

## Resource requirements

The container's resource usage depends on what phase it's in:

| Phase | RAM | CPU | When |
|-------|-----|-----|------|
| Idle (UI running, waiting) | ~100 MB | minimal | Most of the time |
| Analysis (downloading + scoring clips) | 2-4 GB | 2+ cores | First run or new videos, and where most of the wall time goes |
| Assembly (title screens + FFmpeg encode) | 4-8 GB | 4+ cores | Final video generation |

The quickstart compose file sets `memory: 4G` and `cpus: 4`. That's fine for 1080p. For 4K output, bump to 8 GB.

Inside assembly, the title screens cost more than the encode does on a CPU-only box: measured at `--cpus=2`, title rendering was ~263 s of a ~339 s assembly. See [CPU-Only Mode](../hardware/cpu-only.md#title-rendering-is-the-bottleneck-not-encoding) before you size a box around the encoder.

Temporary files during encoding can use 2x the size of your source clips. A 10-minute memory from 50 clips might need 5-10 GB of temp space.

## Standalone Docker run

If you don't use compose:

```bash
docker run -d \
  --name immich-memories \
  -p 8080:8080 \
  -e IMMICH_URL=https://photos.example.com \
  -e IMMICH_API_KEY=your-api-key-here \
  -v immich-memories-config:/home/immich/.immich-memories \
  -v ./output:/app/output \
  ghcr.io/sam-dumont/immich-video-memory-generator:latest
```

## Building the image yourself

Published images include the `all` dependency set. For a local build, make the extras explicit:

```bash
docker build --build-arg APP_VERSION=0.0.0 --build-arg INSTALL_EXTRAS=all -f docker/Dockerfile .
```

From a checkout, `make docker` runs the same build with the version and git metadata filled in
(`INSTALL_EXTRAS=none make docker` for a slim image), and `make docker-run` starts it.
`INSTALL_EXTRAS` is validated at build time; use an explicit supported extra set rather than
assuming a base image happens to include optional features.

## Adding to your existing Immich stack

Drop this into your Immich `docker-compose.yml`. It connects directly to Immich's internal network: no need to expose Immich externally.

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    ports:
      - "8080:8080"
    environment:
      - IMMICH_URL=http://immich-server:2283
      - IMMICH_API_KEY=${IMMICH_API_KEY}
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output   # pre-create and chown to the container UID, see above
    networks:
      - default
    depends_on:
      - immich-server

volumes:
  immich-memories-config:
```

:::tip Immich port
Inside Immich's own compose stack, `immich-server` listens on **2283** (every Immich v2/v3 release; this tool requires Immich v2 or newer). If you're connecting from a separate compose stack, use the URL you open Immich with in your browser instead (for example `http://nas.local:2283`).
:::

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `IMMICH_URL` | Yes | Your Immich server URL |
| `IMMICH_API_KEY` | Yes | Immich API key |
| `IMMICH_MEMORIES_PRESET` | No | `fast` = CPU-only/NAS profile (1080p h264, fast encoder, static titles, no speech pass, favorites-first analysis). Explicit settings win. See the [NAS guide](../common-setups/nas-only.md#one-switch-preset-fast). |
| `IMMICH_MEMORIES_OUTPUT__DIRECTORY` | No | Already `/app/output` in the image. Set it only to write somewhere else — and note it beats `output.directory` in `config.yaml`. |
| `IMMICH_MEMORIES_STORAGE_SECRET` | No | Session secret for the web UI. Auto-generated into the config volume if not set, so sessions already survive a restart. Set it explicitly to share one secret across hosts. It does not make multiple replicas supported. |
| `IMMICH_MEMORIES_LLM__BASE_URL` | No | LLM endpoint (any OpenAI-compatible API). On its own it does nothing for scoring — see the next row. |
| `IMMICH_MEMORIES_LLM__MODEL` | No | LLM model name (e.g., `qwen2.5-vl`) |
| `IMMICH_MEMORIES_CONTENT_ANALYSIS__ENABLED` | No | `true` to actually use the LLM for clip scoring. Off by default. |
| `IMMICH_MEMORIES_AUTH_USERNAME` | No | Basic auth username. Set with `IMMICH_MEMORIES_AUTH_PASSWORD` to enable auth. |
| `IMMICH_MEMORIES_AUTH_PASSWORD` | No | Basic auth password. Set with `IMMICH_MEMORIES_AUTH_USERNAME` to enable auth. |
| `IMMICH_MEMORIES_AUTOMATION__ENABLED` | No | `true` to run the daily `auto run` decision inside the container. Off by default. See [Daily automation](#daily-automation). |
| `IMMICH_MEMORIES_AUTOMATION__DAILY_AT` | No | Wall-clock time for that run, `HH:MM` in the container's `TZ` (default `09:00`). |

All config options can also be set via env vars with the `IMMICH_MEMORIES_` prefix. Double underscores for nesting: `IMMICH_MEMORIES_ANALYSIS__SCENE_THRESHOLD=25`.

## Security hardening

The quickstart compose is intentionally minimal. For production use, add these options:

```yaml
services:
  immich-memories:
    # ... your existing config ...

    # Prevent privilege escalation
    security_opt:
      - no-new-privileges:true

    # Drop all Linux capabilities
    cap_drop:
      - ALL

    # Read-only root filesystem (writes go to tmpfs and volumes)
    read_only: true
    tmpfs:
      - /tmp:size=2G
      - /home/immich/.cache:size=1G

    deploy:
      resources:
        limits:
          memory: 8G
          cpus: "4"
```

The root `docker-compose.yml` has these options as a commented section: uncomment to enable.

Nothing else needs a writable root. The web UI keeps its session storage under
`/home/immich/.immich-memories/.nicegui` on the config volume (the image sets
`NICEGUI_STORAGE_PATH`), so logins survive both a read-only root and a restart.
Don't repoint that variable at `/tmp` or another tmpfs — sessions would work
until the next restart and then quietly log everyone out.

:::caution tmpfs size for 4K
The default tmpfs is 2 GB. If you're generating 4K videos, FFmpeg intermediates can exceed that. Either increase to 8 GB (`/tmp:size=8G`) or remove the tmpfs entry and let the container write to disk.
:::

## Daily automation

The container's only process is the web UI, so there is no cron to install. Turn on the built-in
timer instead:

```yaml
    environment:
      - IMMICH_MEMORIES_AUTOMATION__ENABLED=true
      - IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00
      - TZ=Europe/Brussels   # daily_at is read in this zone
```

Every day at that time the UI process runs the same `auto run` decision as the CLI (retry one
pending upload, or generate one eligible memory, then notify) with the same lock and history. If
the container was down at that time it catches up on start; if the day's run already happened it
waits for tomorrow. Details and the config-file form: [automated generation](../../create/recipes/automated-generation.md#docker-and-the-web-ui-built-in-daily-timer).

Check it from outside with `/health/ready` — the `in_process_scheduler` block shows `next_run`,
`running`, and the last outcome. Automation history stays in the config volume, so keep it
persistent (see below).

## Health check

The Dockerfile health check hits `/health/live`, which reports only that the web process is alive.
It works with Docker's native health reporting and monitoring tools like Uptime Kuma. Use
`/health/ready` for dependency readiness; it returns `200` only when configuration and Immich are
usable, otherwise `503`. `/health` always returns HTTP `200` and rewrites only a ready payload to
`ok`; it is a compatibility endpoint, not the readiness status endpoint.

```bash
# Check health status
docker inspect --format='{{.State.Health.Status}}' immich-memories
```

`/health/live` returns `200` with `status: alive`; `/health/ready` returns the detailed status and
the readiness code above.

## Cache persistence

Analysis scores are cached in `~/.immich-memories/cache.db` (SQLite). This avoids re-running LLM analysis on every generation. The config volume already covers it:

```yaml
volumes:
  - immich-memories-config:/home/immich/.immich-memories  # includes cache.db
```

To back up or migrate the cache separately:

```bash
# Backup
docker exec immich-memories immich-memories cache backup /app/output/cache-backup.db

# Export to JSON (portable)
docker exec immich-memories immich-memories cache export /app/output/scores.json

# Import on a new instance
docker exec immich-memories immich-memories cache import /app/output/scores.json

# Check what's cached
docker exec immich-memories immich-memories cache stats
```

:::tip Migration between hosts
Export to JSON before migrating. The JSON format is portable across SQLite versions and architectures. The binary backup is faster but ties you to the same SQLite version.
:::

## Custom music

In the web UI, pick **Upload file** in Step 3 and the browser uploads the track — no mount needed.
For CLI runs inside the container, bind-mount a directory and point `--music` at it:

```yaml
volumes:
  - ./music:/app/music:ro
```

```bash
docker exec immich-memories immich-memories generate --year 2024 --music /app/music/track.mp3
```

## Updating

```bash
docker compose pull
docker compose up -d
```

Your config and output videos are in named volumes / bind mounts, so nothing is lost on container recreation.
