---
sidebar_label: "NAS-Only (Docker)"
---

# NAS-Only Setup (Docker)

For Synology, QNAP, Unraid, and TrueNAS users running Immich on the same NAS or local network.
The NAS handles the app and CPU encoding; editorial model services may run on another machine.

Before generating, complete [editorial annotation setup](../configuration/editorial-preparation.md).
The story-first route requires its caption and story providers plus the local head/detector
artifacts when their facts are missing. Disabling optional clip-content scoring does not
remove that requirement.

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

![NAS setup diagram](/img/diagrams/setup-nas.png)

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only — see "Reaching the UI" below
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output          # create it first and chown to the container UID, see below
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

### Reaching the UI

The mapping above is loopback-only, so on a headless NAS nothing reaches the UI until you do one
of two things. The cheap one is an SSH tunnel — `ssh -L 8080:localhost:8080 your-nas`, then open
`http://localhost:8080` on your desktop. Nothing is published and there is nothing to secure.

To publish it on the LAN instead, do both halves: turn on
[authentication](../configuration/authentication) first — the app holds an Immich API key to your
whole photo library — then change the mapping to `"8080:8080"`.

One more thing to get right before the first run:

- **Ownership of `./output`**: the image already writes to `/app/output`, so the mount above is
  where the videos land. The container runs as UID/GID 1000. Create the folder yourself
  (`mkdir -p output`) so Docker doesn't create it as root; if your NAS user isn't 1000,
  `chown 1000:1000 output`.
  On Synology/QNAP where that is awkward, use a named volume (`immich-memories-output:/app/output`)
  and `docker cp` the finished video out — or turn on upload-back to Immich and fetch it there.

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
- **Scheduling**: set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` (plus `TZ`) in the compose `environment:` — the UI process runs the daily decision itself, so there is no cron to install. `immich-memories auto install` is for host installs and cannot write a cron job inside the container; if you would rather drive it from the NAS host's scheduler, use `docker exec immich-memories immich-memories auto run --quiet --cooldown 24` and leave the built-in timer off. See [Daily automation](../installation/docker.md#daily-automation)
- **Photo support**: Ken Burns animations, face-aware pan, blur backgrounds

### Editorial work on a NAS

The app prepares facts for the whole source period, then uses the configured story model to
identify stories and distinct moments before allocating duration. The caption and story
services can run elsewhere on your network. Public context heads and detectors use CPU
inference on the app host, or the configured detector Python environment.

Complete cached results skip model work. A missing provider stops an uncached run with an
explicit incomplete result. There is no model-free alternate selector.

## What doesn't work

- **Running large models on a small NAS**: configure reachable caption and story services on
  another machine when the NAS cannot host them. Required missing evidence blocks selection.
- **AI music generation**: MusicGen and ACE-Step need GPU servers. Use custom music upload instead.
- **GPU encoding**: NAS CPUs (Celeron, Atom, low-end Xeon) don't have usable GPU encoders. Encoding is CPU-only via libx264.
- **Taichi GPU title renderer**: falls back to PIL. Title screens still look good, just without particle effects and animated gradients.

## One switch: `preset: fast`

Add `IMMICH_MEMORIES_PRESET=fast` to the compose `environment:` (or `preset: fast` at the top of
`config.yaml`) and the CPU-only profile is on: 1080p H.264 with the fast encoder preset and
medium quality, static title backgrounds instead of animated ones, 
and photos capped at a quarter of the cut. Every value you set explicitly still wins, and the web
UI's options page shows a banner when the preset is active. `immich-memories --preset fast generate …`
does the same for one CLI run.

```yaml
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      IMMICH_MEMORIES_PRESET: "fast"
```

## Performance expectations

One measured number (2026-08-18), so you can calibrate: a monthly memory from a real library,
14 clips (7 videos + 6 photos, HDR iPhone sources), 62 s of 1080p H.264 out, cold cache, the
Docker image with `--cpus=4 --memory=4g` and no GPU (4 cores of an Apple M5 Max running the
linux/arm64 image):

| Profile | Wall time | Analysis | Render | Output |
|---------|-----------|----------|--------|--------|
| `preset: fast` | 10 min 08 s | 7.4 min | 2.7 min | 30 MB |
| default | 15 min 42 s | 10.1 min | 5.6 min | 87 MB |

That run used the old per-clip scorer; on the story-first route the analysis column is
preparation — a caption, context heads and detector facts per picture — plus the text model's
readings, all of it cached, so a second cut of the same month is mostly the render. A Celeron-class NAS core is a good deal slower than an M5 core, so budget 2–3× these
numbers there.

If the render column is what you want to shrink, start with the title screens rather than the
encoder: see [title rendering is the bottleneck](../hardware/cpu-only.md#title-rendering-is-the-bottleneck-not-encoding).

Memory usage peaks at about 2-3 GB during encoding. The 4 GB limit in the compose file gives enough headroom. If you're encoding 4K (not recommended on NAS hardware), bump it to 8 GB.

The streaming assembler keeps memory constant regardless of clip count: it processes one clip at a time instead of loading everything into RAM.

## Tips for NAS users

- **Synology**: use Container Manager (formerly Docker). Create the project from the compose file above.
- **Unraid**: add as a Docker container in the Unraid UI or use Docker Compose Manager plugin.
- **TrueNAS SCALE**: use the built-in Apps system or deploy via custom Docker compose.
- **QNAP**: use Container Station with the compose file.
- Keep the video cache enabled (default). It caches downloaded Immich clips locally, so repeat runs skip the download phase. Default cache limit: 10 GB, evicts files older than 7 days.
