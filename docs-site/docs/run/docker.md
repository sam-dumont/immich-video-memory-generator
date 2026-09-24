---
sidebar_position: 1
title: Docker Compose
---

# Docker Compose

Reader: newcomer and power user.

The reference install: two files from the repo, two values to fill in, one container. It makes
films with no model and no second service. The shortest path through it is the
[Quick start](../get-started/quick-start.md); this page is every step with the reasons.

## Install

You need Docker Engine with Compose v2 (`docker compose version` answers), Immich v2 or v3, and
the hardware on [Requirements](./requirements.md).

**1. Download the compose file and `example.env`** into an empty directory:

```bash
mkdir immich-memories && cd immich-memories
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/example.env
cp example.env .env
```

**2. Fill in `.env`.** Two values are required, the home base is the one that makes trips work:

```bash
IMMICH_URL=http://192.168.1.10:2283       # your Immich, as the container reaches it
IMMICH_API_KEY=your-api-key-here
IMMICH_MEMORIES_TRIPS__HOMEBASE_LATITUDE=50.8503
IMMICH_MEMORIES_TRIPS__HOMEBASE_LONGITUDE=4.3517
TZ=Europe/Brussels
```

`localhost` in `IMMICH_URL` is the container itself, so use the address of your NAS or server.
Every variable `.env` can hold is on [Environment variables](./environment-variables.md). The
compose file also works alone: export `IMMICH_URL` and `IMMICH_API_KEY` in your shell instead.

**3. Create the output folder and start it:**

```bash
mkdir -p output
docker compose up -d
```

The container runs as UID and GID 1000 and writes films to `./output`. If Docker creates that
folder for you, root owns it and the container cannot write there. `mkdir` it first, or fix it in
place with `sudo chown -R 1000:1000 output`. If your user is not 1000, set `user: "<uid>:<gid>"` on
the service and chown the config volume to match.

**4. Fetch the models, once:**

```bash
docker compose exec immich-memories immich-memories models fetch
docker compose exec immich-memories immich-memories preflight
```

`models fetch` downloads about 130 MB: the pinned DINOv2 encoder behind the eight context heads,
the sensitive-content detector, and the document classifier. All three are checked against a
SHA-256 and land on the config volume, so a `docker compose pull` keeps them. `preflight` checks
Immich, the model digests, the home base and whether the output folder takes a file.

**5. Open [http://localhost:8080](http://localhost:8080)** and cut a month:
[Your first film](../get-started/first-film.mdx).

### When a step is missing

A cut (from the web UI, `generate` or `prepare`) checks the models and the output folder before it
asks Immich for anything, and refuses to start if one is wrong:

| It says | Fix |
|---|---|
| `Pinned DINOv2 export missing: ... Run immich-memories models fetch` | Step 4 |
| `Pinned ... export missing: nsfw_marqo has no model: ...` | Step 4 |
| `Output directory is not writable: /app/output: Permission denied` | Step 3: `sudo chown -R 1000:1000 output` |
| `Home coordinates are not configured` (a preflight warning) | Step 2. The cut runs, but no day counts as away from home |

A run with `--no-render` skips the output check; a dry run skips all of them.

## The API key

In Immich: **Account Settings > API Keys > New API Key**. **All** works. The minimal key:

| Permission | Why |
|---|---|
| Read on assets, people, albums, timeline and search | Finding and reading the pictures |
| Read on tags, create tags, tag assets | Marking each uploaded film as this app's own, so a later run never films its own render |
| Upload assets, create and update albums | Upload-back to Immich, if you turn it on |
| Delete assets (optional) | Lets upload-back trash the previous render of the same recipe; without it, old copies pile up |

Your originals are never touched. Without the tag permissions the upload still works, the film is
not tagged, and on Immich v3 a later run cannot recognise its own render.

## Using the CLI

The container has the CLI; the host doesn't. Every `immich-memories ...` command in these docs
runs as `docker compose exec immich-memories immich-memories ...`. An alias saves typing:

```bash
alias im='docker compose exec immich-memories immich-memories'
im generate --memory-type monthly_highlights --year 2025 --month 6
```

## Reaching the UI from another machine

The compose file publishes `127.0.0.1:8080:8080`, so nothing else on your network reaches it.

:::caution Turn on authentication first
Authentication is disabled by default and the app holds an API key to your whole library. The port
mapping is the only thing keeping it off your network. The UI is single-user, single-replica: keep
it at one instance.
:::

Either tunnel (`ssh -L 8080:localhost:8080 your-server`, then `http://localhost:8080` on your
desktop), or do both of these: set `IMMICH_MEMORIES_AUTH_USERNAME` and
`IMMICH_MEMORIES_AUTH_PASSWORD` in `.env` (or OIDC, see [Authentication](./authentication.mdx)),
then change the mapping to `"8080:8080"`. Port 8080 taken already? Change the left side only:
`127.0.0.1:8081:8080`.

## Next to your Immich stack

Paste the `immich-memories` service into Immich's own compose file, set
`IMMICH_URL=http://immich-server:2283`, and add `depends_on: [immich-server]`. It then reaches
Immich over the internal network. `immich-server` listens on 2283 in every Immich v2 and v3
release. An unknown major version stops the run:
[Immich API compatibility](./config-file.md#immich-api-compatibility).

## The preparation tier in compose

The compose file pins `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"`, and an
environment variable beats `config.yaml`: editing `tier:` in the config file inside the container
changes nothing until you edit the compose file too. What each tier runs is on
[Requirements and tiers](./requirements.md#the-preparation-tier).

`IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR` puts the document classifier on the
config volume. Keep that line if you write your own service block: without it the classifier lands
in the container's writable layer and the next `pull` throws it away.

## Add-ons, as profiles

Everything optional sits in the same file, off until you ask for it:

| Add-on | Start it with | Page |
|---|---|---|
| Inference service (heads and detectors on another process or a GPU) | `docker compose --profile inference up -d` | [Inference on a GPU box](../better/inference.md) |
| Caption server (for `tier: full`) | `docker compose --profile captioner up -d` | [Add captions](../better/captions.md) |
| A reader | two env vars pointing at a model server | [Add a reader](../better/reader.md) |
| Hardware encoding (Intel Quick Sync, VA-API) | the commented `devices:` block | [Hardware encoding](./hardware.md) |

Banked facts are the same rows whichever process wrote them, so adding or removing an add-on
re-derives nothing.

### Reaching a model server

Model endpoints must be reachable from inside the container, and `localhost` there is the
container. For a server on the Docker host itself, the name is `host.docker.internal`:

```yaml
      IMMICH_MEMORIES_LLM__BASE_URL: "http://host.docker.internal:8000/v1"
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://host.docker.internal:8092/v1"
```

Docker Desktop resolves it with no setup. On Linux, add this to the service, and have the server
listen on `0.0.0.0` rather than `127.0.0.1` (the container arrives over the bridge):

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## Resources

Idle, the container is about 100 MB. Preparation (previews, heads, detectors) wants 2 to 4 GB and
two cores; the render (titles and the FFmpeg encode) wants 4 to 8 GB and four. The compose limit
is `memory: 4G`: fine for 1080p, give it 8 GB for 4K.

There is no CPU limit, on purpose: `cpus:` is a CFS quota, and a Synology kernel refuses the whole
`up` over it. Use `cpuset: "0-3"` to pin cores instead: [On a NAS](./nas.md#do-not-use-cpus-on-a-synology).

## Hardening

The compose file carries this block commented out; uncomment it:

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

Sessions live under `/home/immich/.immich-memories/.nicegui` on the config volume, so logins
survive a read-only root; don't repoint `NICEGUI_STORAGE_PATH` at a tmpfs. For 4K, raise `/tmp` to
8 GB or drop the entry: FFmpeg's intermediates pass 2 GB.

## Daily automation

The container's only process is the web UI, so there is no cron to install. Uncomment these two in
the compose file's `environment:` block (with `TZ` set in `.env`):

```yaml
      IMMICH_MEMORIES_AUTOMATION__ENABLED: "true"
      IMMICH_MEMORIES_AUTOMATION__DAILY_AT: "09:00"   # read in the TZ zone
```

Every day at that time the UI process runs what `auto run` does on the CLI: retry one pending
upload, or make one eligible memory, then notify. A container that was down catches up on start.
[Automate it](../make/automate.md).

## Health check

The image's health check hits `/health/live`. For readiness, use `/health/ready`: `200` when the
configuration and Immich are usable, `503` otherwise, and it reports the daily automation under
`in_process_scheduler`. `/health` always answers `200` and is not a probe.

```bash
docker inspect --format='{{.State.Health.Status}}' immich-memories
```

## What to keep

`/home/immich/.immich-memories/cache/annotations.sqlite` is the expensive file: every fact,
caption and reading the editor banked. Lose it and the next cut reads the library again.
`cache.db` beside it holds run history and automation state. Both sit on the config volume, so
moving host means copying that volume, and
[the `cache` CLI commands will not do it for you](./maintenance/health-logs-cache.md#the-cli-cache-commands-are-not-for-the-banks).

## Updating

```bash
docker compose pull
docker compose up -d
docker compose exec immich-memories immich-memories models fetch
```

`up` does not re-pull a `latest` the machine already has, hence the `pull`. `models fetch` is a
no-op when the files are right, and downloads again when a release moves a pin. Config, banks and
films live on the volume and the bind mount, so a recreate loses nothing.

## Custom music

**Upload file** on the Generation Options page takes a track from the browser. For CLI runs,
bind-mount a directory and pass `--music /app/music/track.mp3`.

## Building the image

```bash
docker build --build-arg APP_VERSION=0.0.0 --build-arg INSTALL_EXTRAS=all -f docker/Dockerfile .
```

Needs BuildKit (the default since Docker 23). From a checkout, `make docker` fills in the version
and git metadata; `INSTALL_EXTRAS=none make docker` builds the slim image, which cannot run the
heads.
