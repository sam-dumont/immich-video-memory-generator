---
sidebar_position: 1
title: Docker
---

# Install with Docker

Pull the image, set two env vars, done. The editor's models are not in this image: which ones you
need, and what each one costs, is on [Running modes](../running-modes.md). The container itself
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

Get the key from Immich: **Account Settings > API Keys > New API Key**. Pick **All**, or for a
minimal key: read on assets, people, albums, timeline and search, plus asset upload, album
create/update and asset delete if you turn on upload-back. Your originals are never touched. The
one write beyond uploading: when upload-back puts a new render in an album, an earlier upload of
the same recipe in that album is moved to Immich's trash (recoverable) so the album does not fill
with copies. Without the delete permission you get a warning per run and the old copies stay.

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

To reach it from another machine, do both: enable [authentication](../configuration/authentication),
then change the mapping to `"8080:8080"` and `docker compose up -d` again. On a headless box,
tunnel instead: `ssh -L 8080:localhost:8080 your-server`.

The volume at `/home/immich/.immich-memories` must stay writable: config, caches, run history and
automation state live there.

:::caution Who owns ./output
The image writes to `/app/output` (`IMMICH_MEMORIES_OUTPUT__DIRECTORY` is set in the Dockerfile, so
it beats `output.directory` in `config.yaml`) and the compose file mounts `./output` there. The
container runs as UID/GID 1000. Create the folder yourself before the first `up`; if Docker
already created it as root, `sudo chown -R 1000:1000 output` fixes it in place and nothing is
lost. If your user is not 1000: `sudo chown 1000:1000 output`, or set
`user: "<uid>:<gid>"` on the service and chown the config volume the same way, or use a named
volume (`immich-memories-output:/app/output`) and `docker cp` the files out.
:::

## Before the first cut

```bash
docker compose exec immich-memories immich-memories models fetch   # the pinned encoder and both detectors
docker compose exec immich-memories immich-memories preflight      # Immich, the reader, the digests
```

:::caution Put the detector snapshot on the volume first
Two of the three artifacts `models fetch` writes land under `~/.immich-memories`, which is the
config volume. The third, the document classifier's Hugging Face snapshot, goes wherever
`huggingface_hub` puts it, and with `detector_cache_dir` blank that is
`/home/immich/.cache/huggingface` inside the container: the writable layer, which a
`docker compose pull && up -d` throws away. Add this to the service's `environment:` before the
first fetch and it lands on the volume with the rest:

```yaml
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR: "/home/immich/.immich-memories/models/huggingface"
```

Without it, `models fetch` has to be re-run after every recreate, and under the read-only
hardening below (`/home/immich/.cache` on a tmpfs) after every restart.
:::

The compose file pins `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"`, so `models
fetch` is part of the first run: that tier wants the encoder and both detectors. It is the richest
tier the app serves on its own. Drop the key to `metadata_only` and nothing needs fetching; raise it
to `full` once a [caption server](./caption-server.md) answers, and set
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL` with it. What each tier costs and gives
up is on [Running modes](../running-modes.md).

Model endpoints must be reachable from inside the container, and `localhost` there is the
container: give them real hostnames.

## Resources

| Phase | RAM | CPU |
|---|---|---|
| Idle, UI running | small | minimal |
| Preparation (previews, heads, detectors) | 2 to 4 GB | 2+ cores |
| Assembly (title screens + FFmpeg encode) | 4 to 8 GB | 4+ cores |

Those are the working sizes the compose limit (`memory: 4G`) was set around, not a profile of this
image. Fine for 1080p; for 4K, give it 8 GB. On a CPU-only box the title screens cost more than
the encode: at `--cpus=2`, 263 s of a 339 s assembly. See [CPU-only](../hardware.md#without-a-gpu).

The file sets no CPU limit. `cpus:` is a CFS quota and some kernels are built without the
controller: a Synology DS423+ on cgroup v1 refused the whole `up` with `NanoCPUs can not be set,
as your kernel does not support CPU CFS scheduler or the cgroup is not mounted`. To cap the cores,
add `cpuset: "0-3"` to the service, which pins them without a quota, or put `cpus: "4"` back under
`deploy.resources.limits` on a host that has the controller.

The image is 2.37 GB on disk with `INSTALL_EXTRAS=all` on arm64, down from 7.08 GB, because
torch comes from the CPU wheel index and the detectors are ONNX graphs.

## Standalone `docker run`

```bash
docker run -d \
  --name immich-memories \
  -p 127.0.0.1:8080:8080 \
  -e IMMICH_URL=https://photos.example.com \
  -e IMMICH_API_KEY=your-api-key-here \
  -v immich-memories-config:/home/immich/.immich-memories \
  -v ./output:/app/output \
  ghcr.io/sam-dumont/immich-video-memory-generator:latest
```

## Next to your Immich stack

Drop this into Immich's own `docker-compose.yml`; it talks to Immich over the internal network.

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    ports:
      - "127.0.0.1:8080:8080"   # drop the 127.0.0.1: only after enabling auth
    environment:
      - IMMICH_URL=http://immich-server:2283
      - IMMICH_API_KEY=${IMMICH_API_KEY}
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output   # pre-create and chown, see above
    depends_on:
      - immich-server

volumes:
  immich-memories-config:
```

`immich-server` listens on 2283 in every Immich v2 and v3 release. From a separate stack, use the
URL you open Immich with in your browser. Immich v2 and v3 are supported; an unknown major stops
the run (see [Immich API compatibility](../configuration/config-file.md#immich-api-compatibility)).

## Environment variables

| Variable | Description |
|---|---|
| `IMMICH_URL`, `IMMICH_API_KEY` | Required. |
| `IMMICH_MEMORIES_PRESET` | `fast`: 1080p H.264, fast encoder preset, static titles. Explicit settings win. |
| `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER` | `full`, `no_captions` or `metadata_only`. The compose file pins `no_captions`; the code default is `full`. See [Running modes](../running-modes.md). |
| `IMMICH_MEMORIES_LLM__BASE_URL`, `IMMICH_MEMORIES_LLM__MODEL` | The reader. The model string must match what the server reports at `/v1/models`, and it must take images. |
| `IMMICH_MEMORIES_AUTH_USERNAME`, `IMMICH_MEMORIES_AUTH_PASSWORD` | Set both to turn on basic auth. |
| `IMMICH_MEMORIES_STORAGE_SECRET` | Web session secret. Auto-generated into the config volume if unset, so sessions already survive a restart. |
| `IMMICH_MEMORIES_AUTOMATION__ENABLED`, `IMMICH_MEMORIES_AUTOMATION__DAILY_AT` | The daily run inside the container, see below. |
| `IMMICH_MEMORIES_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. |

Every config key has an env var: `IMMICH_MEMORIES_<SECTION>__<FIELD>`, double underscore between
levels. The full list is on [Environment variables](../configuration/environment-variables.md).

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

The web UI keeps its session storage under `/home/immich/.immich-memories/.nicegui` on the config
volume (the image sets `NICEGUI_STORAGE_PATH`), so logins survive a read-only root and a restart.
Do not repoint that variable at a tmpfs. For 4K, FFmpeg intermediates can exceed 2 GB of `/tmp`:
raise it to 8 GB or drop the tmpfs entry.

## Daily automation

The container's only process is the web UI, so there is no cron to install:

```yaml
    environment:
      - IMMICH_MEMORIES_AUTOMATION__ENABLED=true
      - IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00
      - TZ=Europe/Brussels   # daily_at is read in this zone
```

Every day at that time the UI process runs the same decision as `auto run` on the CLI: retry one
pending upload, or generate one eligible memory, then notify. If the container was down at that
time it catches up on start. `/health/ready` shows `next_run`, `running` and the last outcome under
`in_process_scheduler`. Details: [automated generation](../../create/recipes/automated-generation.md).

## Health check

The Dockerfile health check hits `/health/live` (the web process answers). Use `/health/ready` for
readiness: `200` when configuration and Immich are usable, `503` otherwise. `/health` always
returns `200` and is a compatibility endpoint, not a readiness probe.

```bash
docker inspect --format='{{.State.Health.Status}}' immich-memories
```

## What to keep

The expensive file is `~/.immich-memories/cache/annotations.sqlite`: every caption, head answer,
detector verdict and reading the editor has banked. Lose it and the next cut re-reads the library.
`cache.db` beside it holds run history and automation state. Both sit on the config volume, so
moving to a new host means copying that volume.
[`immich-memories cache backup|export|import` will not do it](../maintenance/health-logs-cache.md#the-cli-cache-commands-are-not-for-the-banks):
those three move the retired scorer's table and leave the banks behind.

## Custom music

In the web UI, **Upload file** on the Generation Options page uploads the track from the browser.
For CLI runs inside the container, bind-mount a directory and pass `--music /app/music/track.mp3`.

## Building the image

```bash
docker build --build-arg APP_VERSION=0.0.0 --build-arg INSTALL_EXTRAS=all -f docker/Dockerfile .
```

Needs BuildKit (the default since Docker 23). From a checkout, `make docker` fills in the version
and git metadata (`INSTALL_EXTRAS=none make docker` for a slim image). `INSTALL_EXTRAS` is
validated at build time.

## Updating

```bash
docker compose pull
docker compose up -d
```

Config and videos live in the volume and the bind mount; nothing is lost on recreate.
