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

Get the API key from Immich: **Account Settings > API Keys > New API Key**.

Then grab the compose file from the repo and start it:

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
docker compose up -d
```

UI is at [http://localhost:8080](http://localhost:8080).

## Resource requirements

The container's resource usage depends on what phase it's in:

| Phase | RAM | CPU | When |
|-------|-----|-----|------|
| Idle (UI running, waiting) | ~100 MB | minimal | Most of the time |
| Analysis (downloading + scoring clips) | 2-4 GB | 2+ cores | First run or new videos |
| Encoding (FFmpeg assembly) | 4-8 GB | 4+ cores | Final video generation |

The quickstart compose file sets `memory: 4G` and `cpus: 4`. That's fine for 1080p. For 4K output, bump to 8 GB.

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

## Adding to your existing Immich stack

Drop this into your Immich `docker-compose.yml`. It connects directly to Immich's internal network: no need to expose Immich externally.

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    ports:
      - "8080:8080"
    environment:
      - IMMICH_URL=http://immich_server:3001
      - IMMICH_API_KEY=${IMMICH_API_KEY}
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output
    networks:
      - default
    depends_on:
      - immich-server

volumes:
  immich-memories-config:
```

:::tip Immich port
If you're connecting from a separate compose stack (not added to Immich's), use the external port. The internal port varies by Immich version: 2283 for older versions, 3001 for newer ones. When in doubt, use whatever URL you access Immich from in your browser.
:::

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `IMMICH_URL` | Yes | Your Immich server URL |
| `IMMICH_API_KEY` | Yes | Immich API key |
| `IMMICH_MEMORIES_STORAGE_SECRET` | No | Session secret for the web UI. Auto-generated if not set. Set this explicitly if you run multiple replicas or restart frequently (avoids session invalidation). |
| `IMMICH_MEMORIES_LLM__BASE_URL` | No | LLM endpoint for content analysis (any OpenAI-compatible API) |
| `IMMICH_MEMORIES_LLM__MODEL` | No | LLM model name (e.g., `qwen2.5-vl`) |
| `IMMICH_MEMORIES_AUTH_USERNAME` | No | Basic auth username. Set with `IMMICH_MEMORIES_AUTH_PASSWORD` to enable auth. |
| `IMMICH_MEMORIES_AUTH_PASSWORD` | No | Basic auth password. Set with `IMMICH_MEMORIES_AUTH_USERNAME` to enable auth. |

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

:::caution tmpfs size for 4K
The default tmpfs is 2 GB. If you're generating 4K videos, FFmpeg intermediates can exceed that. Either increase to 8 GB (`/tmp:size=8G`) or remove the tmpfs entry and let the container write to disk.
:::

## Health check

The container has a built-in health check hitting `/health`. Works with Docker's native health reporting and monitoring tools like Uptime Kuma:

```bash
# Check health status
docker inspect --format='{{.State.Health.Status}}' immich-memories
```

The `/health` endpoint returns JSON with `status`, `immich_reachable`, `last_successful_run`, and `version`.

## Cache persistence

Analysis scores are cached in `~/.immich-memories/cache.db` (SQLite). This avoids re-running LLM analysis on every generation. The config volume already covers it:

```yaml
volumes:
  - immich-memories-config:/home/immich/.immich-memories  # includes cache.db
```

To back up or migrate the cache separately:

```bash
# Backup
docker exec immich-memories immich-memories cache backup /output/cache-backup.db

# Export to JSON (portable)
docker exec immich-memories immich-memories cache export /output/scores.json

# Import on a new instance
docker exec immich-memories immich-memories cache import /output/scores.json

# Check what's cached
docker exec immich-memories immich-memories cache stats
```

:::tip Migration between hosts
Export to JSON before migrating. The JSON format is portable across SQLite versions and architectures. The binary backup is faster but ties you to the same SQLite version.
:::

## Custom music

To use local music files, bind-mount a directory:

```yaml
volumes:
  - ./music:/app/music:ro
```

Then set `audio.local_music_dir: /app/music` in your config.

## Updating

```bash
docker compose pull
docker compose up -d
```

Your config and output videos are in named volumes / bind mounts, so nothing is lost on container recreation.
