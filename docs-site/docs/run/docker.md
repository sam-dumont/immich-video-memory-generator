---
sidebar_position: 1
title: Docker
---

# Install with Docker

Pull the image, set two env vars, done. The editor's models are not in this image: which ones you
need, and what each one costs, is on [Running modes](../being-rewritten/running-modes.md). The container itself
wants 2 to 4 GB.

## Quick start

From an empty directory:

```bash
mkdir -p immich-memories/output && cd immich-memories
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
```

Then a `.env` beside it:

```bash
IMMICH_URL=https://photos.example.com
IMMICH_API_KEY=your-api-key-here
```

Get the key from Immich: **Account Settings > API Keys > New API Key**. Pick **All**, or a minimal
key: read on assets, people, albums, timeline, search and tags, plus asset upload, album
create/update and tag create/asset for upload-back. The tag marks each uploaded film as this app's
own, so a later run never films it again and can tell it apart from your videos. Your originals are
never touched. Asset delete is optional and only lets upload-back trash the previous render of the
same recipe; without it old copies pile up.

```bash
docker compose up -d
```

The UI is at [http://localhost:8080](http://localhost:8080). The compose file publishes the port as
`127.0.0.1:8080:8080`, so nothing else on your network reaches it.

:::caution Do not publish the default UI
Authentication is disabled by default. Inside the container the app listens on `0.0.0.0`, so the
port mapping is the only thing keeping it off your network, and the app holds an Immich API key to
your whole library. The UI is single-user, single-replica; keep it at one instance.
:::

To reach it from another machine, do both: enable [authentication](./authentication)
and change the mapping to `"8080:8080"`. On a headless box, tunnel instead:
`ssh -L 8080:localhost:8080 your-server`.

The volume at `/home/immich/.immich-memories` must stay writable: config, caches, run history and
automation state live there.

:::caution Who owns ./output
The image writes to `/app/output` (`IMMICH_MEMORIES_OUTPUT__DIRECTORY` in the Dockerfile beats
`output.directory` in `config.yaml`) and compose mounts `./output` there. The container is UID/GID
1000, so create the folder before the first `up`; if Docker made it as root,
`sudo chown -R 1000:1000 output` fixes it in place. If your user is not 1000, set
`user: "<uid>:<gid>"` on the service and chown the config volume to match.
:::

## Before the first cut

```bash
docker compose exec immich-memories immich-memories models fetch   # the pinned encoder and both detectors
docker compose exec immich-memories immich-memories preflight      # Immich, the reader, the digests
```

All three artifacts land on the config volume. Two go there on their own; the third, the document
classifier's snapshot, goes there because the compose file sets
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR` to a path on it. Keep that line if you
write your own service block: without it the snapshot sits in the container's writable layer, which
a `pull && up -d` throws away.

Preflight's `Title rendering` row names what draws the titles. With no GPU passed into the
container it reads `Kernels on the CPU (quadrants): no GPU backend started`: the animated title
kernels run on the processor, which is expected and slower. A row that says `PIL renderer` means an
old image. `docker compose up` does not re-pull a `latest` that is already on the machine, so run
`docker compose pull` first if you tried the app before.

The compose file pins `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"`, the richest
tier the app serves on its own, and that tier is why `models fetch` is part of the first run.
`metadata_only` needs no fetch at all; `full` needs a [caption server](../better/captions.md) and
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL` pointing at it. What each tier costs is
on [Running modes](../being-rewritten/running-modes.md).

### Reaching a model server

Model endpoints must be reachable from inside the container, and `localhost` there is the
container: give them real hostnames. For a reader or caption server on the Docker host itself, that
name is `host.docker.internal`:

```yaml
      IMMICH_MEMORIES_LLM__BASE_URL: "http://host.docker.internal:8000/v1"
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://host.docker.internal:8092/v1"
```

Docker Desktop (Mac, Windows) resolves it with no setup. On Linux, add this to the
`immich-memories` service, and have the server on the host listen on `0.0.0.0` rather than
`127.0.0.1`, since the container arrives over the bridge, not the loopback:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## Resources

Idle, it is small. Preparation (previews, heads, detectors) wants 2 to 4 GB and two cores;
assembly (title screens and the FFmpeg encode) wants 4 to 8 GB and four. Those are the sizes the
compose limit (`memory: 4G`) was set around: fine for 1080p, give it 8 GB for 4K. On a CPU-only box
the encode is the larger half of a run, see [CPU-only](./hardware.md#without-a-gpu).

The file sets no CPU limit, deliberately: `cpus:` is a CFS quota and a Synology kernel refuses the
whole `up` over it. Use `cpuset: "0-3"` there instead, see [NAS](../being-rewritten/nas-only.md).

The image is 2.37 GB on disk with `INSTALL_EXTRAS=all` on arm64.

## Next to your Immich stack

Paste the `immich-memories` service from `docker-compose.yml` into Immich's own compose file, set
`IMMICH_URL=http://immich-server:2283` and add `depends_on: [immich-server]`. It then reaches
Immich over the internal network. `immich-server` listens on 2283 in every Immich v2 and v3
release; from a separate stack, use the URL you open Immich with. An unknown major stops the run,
see [Immich API compatibility](./config-file.md#immich-api-compatibility).

## Environment variables

| Variable | Description |
|---|---|
| `IMMICH_URL`, `IMMICH_API_KEY` | Required. |
| `IMMICH_MEMORIES_PRESET` | `fast`: 1080p H.264, fast encoder preset, static titles. Explicit settings win. |
| `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER` | `full`, `no_captions` or `metadata_only`. The compose file pins `no_captions`; the code default is `full`. |
| `IMMICH_MEMORIES_LLM__BASE_URL`, `IMMICH_MEMORIES_LLM__MODEL` | The reader. The model string must match what the server reports at `/v1/models`, and it must take images. |
| `IMMICH_MEMORIES_LLM__API_KEY` | The reader's bearer token, for a server that answers `401` without one. |
| `IMMICH_MEMORIES_AUTH_USERNAME`, `IMMICH_MEMORIES_AUTH_PASSWORD` | Set both to turn on basic auth. |
| `IMMICH_MEMORIES_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. |

Every config key has an env var: `IMMICH_MEMORIES_<SECTION>__<FIELD>`, double underscore between
levels. The full list is on [Environment variables](./environment-variables.md).

## Hardening

The root `docker-compose.yml` carries this block commented out; uncomment it:

```yaml
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:size=2G
      - /home/immich/.cache:size=1G
```

Session storage sits under `/home/immich/.immich-memories/.nicegui` on the config volume, so logins
survive a read-only root; do not repoint `NICEGUI_STORAGE_PATH` at a tmpfs. For 4K, raise `/tmp` to
8 GB or drop the entry: FFmpeg intermediates exceed 2 GB.

## Daily automation

The container's only process is the web UI, so there is no cron to install:

```yaml
    environment:
      - IMMICH_MEMORIES_AUTOMATION__ENABLED=true
      - IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00
      - TZ=Europe/Brussels   # daily_at is read in this zone
```

Every day at that time the UI process runs the same decision as `auto run` on the CLI: retry one
pending upload, or generate one eligible memory, then notify. A container that was down catches up
on start, and `/health/ready` reports it under `in_process_scheduler`. Details:
[automated generation](../make/automate.md).

## Health check

The Dockerfile health check hits `/health/live`. Use `/health/ready` for readiness: `200` when
configuration and Immich are usable, `503` otherwise. `/health` always returns `200` and is a
compatibility endpoint, not a probe.

```bash
docker inspect --format='{{.State.Health.Status}}' immich-memories
```

## What to keep

`~/.immich-memories/cache/annotations.sqlite` is the expensive file: every caption, head answer,
detector verdict and reading the editor has banked. Lose it and the next cut re-reads the library.
`cache.db` beside it holds run history and automation state. Both sit on the config volume, so
moving host means copying that volume, and
[the `cache` CLI commands will not do it for you](./maintenance/health-logs-cache.md#the-cli-cache-commands-are-not-for-the-banks).

## Custom music

**Upload file** on the Generation Options page takes a track from the browser. For CLI runs,
bind-mount a directory and pass `--music /app/music/track.mp3`.

## Building the image

```bash
docker build --build-arg APP_VERSION=0.0.0 --build-arg INSTALL_EXTRAS=all -f docker/Dockerfile .
```

Needs BuildKit (the default since Docker 23). From a checkout, `make docker` fills in the version
and git metadata; `INSTALL_EXTRAS=none make docker` builds the slim image.

## Updating

```bash
docker compose pull
docker compose up -d
```

Config and videos live in the volume and the bind mount; nothing is lost on recreate.
