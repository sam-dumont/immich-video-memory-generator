---
sidebar_label: "NAS + a model box"
---

# A NAS, and one machine that can hold the models

For Synology, QNAP, Unraid and TrueNAS users who already run Immich on the box.

A NAS can cut a memory on its own with the rules reader, or use a model on another machine.
The app, the caches, image preparation and the render all run on the NAS. The one thing a
tested DS423+ cannot hold is the 30B vision reader (about 17 GB resident at 4-bit): that goes
on a Mac with 32 GB or a box with a 24 GB GPU, or you skip it with `reader: rules`.
[Running modes](../running-modes.md) has the measured cost of each choice; this page is the
NAS-shaped version of the [self-hosting guide](../self-hosting.md).

## NAS alone, no model

```yaml
advanced:
  editorial:
    reader: rules
    preparation:
      tier: metadata_only
```

All ten memory types work, albums and person memories included; only custom free-text subjects
need a model. Measured on a DS423+ (Celeron J4125, 4 cores): a one-month selection took 279 s cold
and 11.1 s on repeat, with zero model requests, rendering excluded. The rules cut can skip an
occasion or spend a slot on a mundane object. Look at it before you share it.

Add `no_captions` once `models fetch` has run (below): the two detectors and the six context heads
then give the family-viewing gate real evidence.

## Preparation tiers: what the NAS pays

`editorial.preparation.tier` names which producers this box runs. A producer the tier demands and
cannot reach stops the run with a count per missing producer; a producer the tier does not ask
for is never missing.

| `tier` | What runs | First pass over about ten thousand pictures, DS423+ |
|---|---|---|
| `full` | pixels, encoder + six heads, both detectors, one caption per picture | 4 days |
| **`no_captions`**, the NAS tier | pixels, encoder + six heads, both detectors | **3 h 41 min** |
| `metadata_only` | pixels and Immich metadata; no ONNX, no captions | minutes |

Measured per picture on that box: 1.23 s for every producer except the caption (the DINOv2 embed
is 0.53 s of it, the sensitive-content detector 0.40 s), against 30.9 s for one caption. Peak
resident memory 570 MB. Every producer banks its answer per picture, so these are first-pass costs:
the second cut over the same month pays a preview check and the render.

What `no_captions` costs you: no description under each picture (the reason is the facts that
funded it, and the Memory page says so), and the gate can refuse but never clear. Eight findings
that hold a picture to family viewing (bathing, toileting, intimate hygiene, a medical procedure,
an identifying record and the rest) are only named by a description, so every unit stays at family
viewing and a `sendable` export needs the `full` tier. `metadata_only` has no evidence at all and
refuses `sendable` outright.

Captions bank per picture too, so a `no_captions` NAS can add them later, a month at a time.

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only, see below
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - immich-memories-library-cache:/home/immich/.cache
      - immich-memories-scratch:/tmp
      - ./output:/app/output          # mkdir it first; chown 1000:1000 if your user is not 1000
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"
      # With a model on another machine. Not localhost: inside a container that is the container.
      # IMMICH_MEMORIES_LLM__BASE_URL: "http://model-box.lan:8000/v1"
      # IMMICH_MEMORIES_LLM__MODEL: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
      # Only on the full tier:
      # IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://model-box.lan:8092/v1"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G

volumes:
  immich-memories-config:
  immich-memories-library-cache:
  immich-memories-scratch:
```

`llm.model` must be the exact string the reader reports at `GET /v1/models`. On the `full` tier
the caption endpoint must advertise the alias `smolvlm2-500m-base-public` at `/models`; the client
checks that and three schema controls before it sends a single preview.

The container runs as UID/GID 1000. Create `./output` yourself so Docker does not create it as
root. On Synology or QNAP, where that is awkward, use a named volume
(`immich-memories-output:/app/output`) and `docker cp` the video out, or turn on upload-back to
Immich.

### Reaching the UI

The mapping above is loopback-only, so on a headless NAS the cheap way in is an SSH tunnel:
`ssh -L 8080:localhost:8080 your-nas`, then `http://localhost:8080` on your desktop. To publish it
on the LAN instead, turn on [authentication](../configuration/authentication) first (the app
holds an API key to your whole library), then change the mapping to `"8080:8080"`.

## The first run, start to finish

Start with one month. Preparation scales with the width of the date range, not with the length
of the video.

```bash
docker compose up -d
docker compose exec immich-memories immich-memories models fetch     # the encoder and both detectors, once
docker compose exec immich-memories immich-memories preflight        # Immich, the reader, the model digests
docker compose exec immich-memories immich-memories generate --type monthly --duration 60
```

`models fetch` writes the two digest-pinned ONNX exports (the 88 MB DINOv2-small encoder and the
22.5 MB sensitive-content detector) and warms the document classifier's snapshot into the library-cache
volume. All three run on the CPU. A producer that cannot load its model says so by name in the
first seconds of the detector stage, and names the command that fixes it.

The run prints one line per cut with the per-producer cost
(`preparation tier=no_captions: 1234 pictures requested; detectors 0.396s/pic ...`), so
`docker compose logs immich-memories | grep "preparation tier"` tells you which producer a box
cannot afford. Run the same month twice: the difference is the cold cost.

### Adding captions later

Point the container at a caption endpoint and run the same scope again on the `full` tier:

```bash
docker compose exec \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=full \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL=http://model-box.lan:8092/v1 \
  immich-memories immich-memories generate --type monthly --duration 60
```

About 2,700 pictures a day on a DS423+. Do it a month at a time, overnight, then set the compose
tier to `full` once you are caught up. There is no background backfill job.

## Encoding on an Intel NAS

On an Intel NAS, check `vainfo` for the codecs the device can encode. The default
`codec_policy: prefer_hardware` can choose H.264 when HEVC hardware encoding is unavailable for
an SDR render. `strict` keeps the requested codec and uses software if needed. Pass the device through:

```yaml
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "937"        # the GID that owns /dev/dri/renderD128 on YOUR host
```

The render node is usually `root:render` with no world access, so `devices:` alone leaves it
present and unopenable. The GID differs per host: 104 on Debian, 937 on Synology DSM;
`stat -c '%g' /dev/dri/renderD128` prints yours. ARM NAS models have no Quick Sync path; the
VA-API drivers ship in the amd64 image only. Details on [Intel Quick Sync](../hardware/intel-qsv.md).

## One switch: `preset: fast`

`IMMICH_MEMORIES_PRESET=fast` in the compose `environment:` (or `preset: fast` at the top of
`config.yaml`) sets 1080p H.264 at the fast quality tier with the fast encoder preset and static
title backgrounds. Five keys, nothing about what the editor reads. Any value you set explicitly
still wins, and the Generation Options page shows a banner while the preset is active.

## CPU limits on NAS kernels

The example above leaves the CPU quota unset. Some Synology kernels reject Compose's
`deploy.resources.limits.cpus` with `NanoCPUs can not be set`. If you downloaded the repository's
Compose file instead, remove that entry from both services on an affected host. Keep memory
limits. A `cpuset` using CPU IDs that exist on your NAS is an alternative when you need to
restrict scheduling. [PR #929](https://github.com/sam-dumont/immich-video-memory-generator/pull/929)
updates the shipped defaults; it is pending at the time of this review.

## Memory and disk

The 4 GB limit in the compose file is the one the measured runs used. The streaming assembler
processes clips sequentially, reducing the peak compared with loading all clips together.
Resolution, title settings and source decoding still affect memory. 4K on NAS hardware is
not recommended; raise the limit if you try.

Default media cache budgets total about 22 GB. Active files can exceed those budgets, and
annotations, attempts, models, temporary renders and exports need more space. A thumbnail
working set that does not fit produces a warning and can require downloads again on later runs.
Use `runs storage` to inspect usage and keep the configured cache directory on persistent disk.

## Limits to check

- A small NAS cannot hold the tested 30B reader. Use another machine or `reader: rules`.
  `no_captions` alone does not turn a configured reader off.
- Local music models need their own memory budget. A supplied or bundled track avoids that cost.
- Title rendering depends on the CPU and available kernel backend. On the tested J4125, the
  kernel library crashes during loading; a child-process probe catches it and selects PIL.
  PIL keeps text animation and simpler backgrounds. See [CPUs without AVX](../hardware/cpu-only.md#cpus-without-avx).

## NAS notes

- **Synology**: Container Manager, create the project from the compose file above.
- **Unraid**: add it as a Docker container or use the Docker Compose Manager plugin.
- **TrueNAS SCALE**: the Apps system, or a custom compose.
- **QNAP**: Container Station with the compose file.
- **Scheduling**: `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
  `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` (plus `TZ`) in `environment:`. The UI process runs
  the daily decision itself; there is no cron to install. See [Daily automation](../installation/docker.md#daily-automation).
