---
sidebar_label: "NAS-Only (Docker)"
---

# NAS-Only Setup (Docker)

For Synology, QNAP, Unraid, and TrueNAS users running Immich on the same NAS or local network. Docker-only, no LLM, no AI music, CPU encoding.

## Who this is for

You have a NAS with Docker support (Synology DSM 7+, Unraid, TrueNAS SCALE, QNAP Container Station). You're already running Immich there. You want memory videos without setting up Python environments or GPU passthrough.

## Architecture

```
┌─────────────────────────────────────────┐
│ NAS (Synology/Unraid/TrueNAS)          │
│                                         │
│  ┌─────────────┐  ┌──────────────────┐ │
│  │   Immich     │  │ Immich Memories  │ │
│  │  (port 2283) │←─│  (port 8080)    │ │
│  │             │  │  CPU encoding    │ │
│  │             │  │  PIL titles      │ │
│  └─────────────┘  └──────────────────┘ │
│                                         │
│  Volumes: config, output, video cache   │
└─────────────────────────────────────────┘
```

:::info Screenshot needed
**What to capture:** Architecture diagram for NAS-only setup (create as a proper diagram later)
**Viewport:** 1280x800
**State:** Box diagram showing NAS with Immich and Immich Memories containers
**Target file:** `static/screenshots/setup-nas-diagram.png`
:::

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "8080:8080"
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

volumes:
  immich-memories-config:
```

## .env file

```bash
IMMICH_URL=http://immich-server:2283
IMMICH_API_KEY=your-api-key-here
```

If Immich runs on the same Docker network, use the container name (`immich-server`). If it's on a different machine or behind a reverse proxy, use the full URL (`https://photos.example.com`).

## What works

- **Clip scoring**: motion analysis, face detection (CPU-based), favorites boost, audio signals
- **Title screens**: PIL-based renderer (works everywhere, no GPU needed)
- **Custom music**: upload your own MP3/WAV in Step 3
- **All memory types**: year in review, monthly, person spotlight, trips (if GPS data exists)
- **Scheduling**: cron-based automated generation via the CLI
- **Photo support**: Ken Burns animations, face-aware pan, blur backgrounds

## What doesn't work

- **LLM content analysis**: needs a separate LLM server (mlx-vlm, Ollama, vLLM). Without it, scoring uses motion + faces + audio only: still good, just not as context-aware.
- **AI music generation**: MusicGen and ACE-Step need GPU servers. Use custom music upload instead.
- **GPU encoding**: NAS CPUs (Celeron, Atom, low-end Xeon) don't have usable GPU encoders. Encoding is CPU-only via libx264.
- **Taichi GPU title renderer**: falls back to PIL. Title screens still look good, just without particle effects and animated globes.

## Performance expectations

On a typical NAS CPU (Intel Celeron J4125, 4 cores, 2.0 GHz):

| Clips | Resolution | Time |
|-------|-----------|------|
| 15 | 1080p | ~8 min |
| 30 | 1080p | ~15 min |
| 30 | 720p | ~10 min |
| 50 | 1080p | ~25 min |

Memory usage peaks at about 2-3 GB during encoding. The 4 GB limit in the compose file gives enough headroom. If you're encoding 4K (not recommended on NAS hardware), bump it to 8 GB.

The streaming assembler keeps memory constant regardless of clip count: it processes one clip at a time instead of loading everything into RAM.

## Tips for NAS users

- **Synology**: use Container Manager (formerly Docker). Create the project from the compose file above.
- **Unraid**: add as a Docker container in the Unraid UI or use Docker Compose Manager plugin.
- **TrueNAS SCALE**: use the built-in Apps system or deploy via custom Docker compose.
- **QNAP**: use Container Station with the compose file.
- Keep the video cache enabled (default). It caches downloaded Immich clips locally, so repeat runs skip the download phase. Default cache limit: 10 GB, evicts files older than 7 days.
