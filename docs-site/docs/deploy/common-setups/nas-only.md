---
sidebar_label: "NAS + a model box"
---

# A NAS, and one machine that can hold the models

For Synology, QNAP, Unraid and TrueNAS users who already run Immich on the box.

The app, caches and image preparation run on the NAS. Rendering also runs there by default;
a [GPU render worker](../running-modes.md#rendering-on-another-machine) can take that stage.
The one thing a tested
DS423+ cannot hold is the 30B vision reader (about 17 GB resident at 4-bit): that goes on a Mac
with 32 GB or a box with a 24 GB GPU, or you skip it with `reader: rules`. This is the NAS-shaped
version of the [self-hosting guide](../self-hosting.md).

## NAS alone, no model

```yaml
advanced:
  editorial:
    reader: rules
    preparation:
      tier: metadata_only
```

All ten memory types work, albums and person memories included; only custom free-text subjects
need a model. The rules cut can skip an occasion or spend a slot on a mundane object, so look at it
before you share it. Move to `no_captions` once `models fetch` has run (below): the detectors and
the six context heads then give the family-viewing gate real evidence.

## Preparation tiers: what the NAS pays

`editorial.preparation.tier` names which producers this box runs.

| `tier` | First pass over about ten thousand pictures, DS423+ |
|---|---|
| `full` | 4 days |
| **`no_captions`**, the NAS tier | **3 h 41 min** |
| `metadata_only` | minutes |

Measured per picture on that box: 1.23 s for every producer except the caption (the DINOv2 embed
is 0.53 s of it, the sensitive-content detector 0.40 s), against 30.9 s for one caption. Peak
resident memory 570 MB. Every producer banks its answer, so these are first-pass costs.

What each tier hands the editor is on [Running modes](../running-modes.md#the-preparation-tier).
Below `full` the family-viewing gate can refuse but never clear, so a `sendable` export needs
captions.

## Docker Compose

If you have an NVIDIA box or cluster, you can also
[let it render](#let-the-gpu-box-render).

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only, see below
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
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
    # cpuset, not cpus: see below. Drop the line to leave the cores uncapped.
    cpuset: "0-3"
    deploy:
      resources:
        limits:
          memory: 4G

volumes:
  immich-memories-config:
```

`llm.model` must be the exact string the reader reports at `GET /v1/models`. The container runs as
UID/GID 1000, so create `./output` before the first start. On Synology or QNAP, where that is
awkward, use a named volume and `docker cp` the video out, or turn on upload-back to Immich.

### Do not cap the CPU with `cpus:` on a Synology

`cpus:` is a CFS quota, and DSM runs a cgroup v1 kernel built without the CFS bandwidth controller.
A DS423+ answers `docker compose up` with `NanoCPUs can not be set, as your kernel does not support
CPU CFS scheduler or the cgroup is not mounted` and starts nothing at all. `cpuset: "0-3"` pins the
same four cores and works. Memory limits are fine on every NAS tested.

### Reaching the UI

The mapping above is loopback only, so on a headless NAS use an SSH tunnel:
`ssh -L 8080:localhost:8080 your-nas`, then `http://localhost:8080` on your desktop. To put it on
the LAN instead, turn on [authentication](../configuration/authentication) first (the app holds an
API key to your whole library), then change the mapping to `"8080:8080"`.

## The first run, start to finish

Start with one month. Preparation scales with the width of the date range, not the length of the
video.

```bash
docker compose up -d
docker compose exec immich-memories immich-memories models fetch     # the encoder and both detectors, once
docker compose exec immich-memories immich-memories preflight        # Immich, the reader, the model digests
docker compose exec immich-memories immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

`models fetch` puts the two digest-pinned ONNX exports on the config volume, but the document
classifier's Hugging Face snapshot lands in the container's writable layer and is gone on the next
`docker compose pull`. Add
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR: "/home/immich/.immich-memories/models/huggingface"`
to `environment:` before you fetch and all three stay on the volume.

Each cut logs its per-producer cost, so
`docker compose logs immich-memories | grep "preparation tier"` tells you which producer this box
cannot afford. Plan an overnight for the first pass over a big month and seconds for every pass
after it: [Running modes](../running-modes.md#what-to-expect-on-a-first-run) has this box measured
per phase against a Mac and a Kubernetes cluster. The render is what a warm cache never helps.
Moving the picture facts to a GPU box with the
[inference service](../installation/inference-service.md) took this NAS from 1.4404 s a picture to
1.0865 on the demo month.

## Let the GPU box render

Deploy the
[render worker](https://github.com/sam-dumont/immich-video-memory-generator/tree/main/services/render-worker)
on the GPU box, using the same app version. Add this to the NAS app's configuration:

```yaml
render:
  worker_base_url: https://render.example.com
  worker_token: ${RENDER_WORKER_TOKEN}
  fallback_to_local: false
```

Pass the same worker token to both processes, then run `immich-memories preflight -v`.
The NAS sends the selected cut and its scoped Immich key. The worker downloads
the originals directly, renders and returns the film; the NAS checks the result
before music or upload. Selection, speech-safe cuts and stitched Live durations
stay intact.

A real Synology-to-NVIDIA T1000 replay completed a 55-second, 15-clip 1080p H.265
film in **9 min 26 s**, including transfer and full decode validation on the NAS.
That used an existing February cut and made no model calls. Fresh preparation
and selection are additional work; this is not a first-run estimate.

## A reader you do not host

With no second machine, the third option is a provider: same `model` reader, same contract, and
800 px tiles of the few dozen candidates the edit asks about leave your network, along with their
annotation lines and the people and place names on them.

```yaml
      IMMICH_MEMORIES_LLM__PROVIDER: "openai"      # ollama | openai-compatible | openai | zai | anthropic
      IMMICH_MEMORIES_LLM__MODEL: "gpt-4.1-mini"
      IMMICH_MEMORIES_LLM__API_KEY: "${OPENAI_API_KEY}"
```

The model has to take images and hold a 32k context. Leave `llm.base_url` unset and the provider
name fills in its own vendor URL. Ten readers were pointed at one real month, three stopped, and
the rest cost EUR 0.054 to EUR 0.585 of tokens: [Readers](../readers.md). Every outbound request is
listed on [Network & Privacy](../configuration/network-and-privacy.md).

### Adding captions later

The shipped `docker-compose.yml` carries a caption server behind a profile, so a NAS with the
patience for it needs no second box.

```bash
docker compose --profile captioner up -d
curl -s localhost:8094/v1/models
```

Leave `IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_CONCURRENCY` at 1: four image encodes at
once share the threads of one, so raising it is slower. At 30.9 s a caption on four Celeron cores a
13,552-picture year is almost five days, which is why this page recommends `no_captions`.
[Caption server](../installation/caption-server.md) has the flags.

Or point the container at a caption endpoint on another box and run the same scope on `full`:

```bash
docker compose exec \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=full \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL=http://model-box.lan:8092/v1 \
  immich-memories immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

About 2,700 pictures a day on a DS423+. Do it a month at a time, then set the compose tier to
`full` once you are caught up. There is no background backfill job.

## Encoding on an Intel NAS

Intel Gemini Lake (the J4125 class) has an H.264 encode entrypoint and no HEVC one, and the default
output codec is H.265. The backend encodes what the chip can and sends the rest to libx265, logging
`vaapi cannot encode h265 on this device; encoding it in software`. Set `output.codec: h264`, or
turn on `preset: fast`, which drops the whole render to 1080p H.264 at the fast quality tier.
Passing `/dev/dri` through needs the host GID that owns the render node in
`group_add` as well (937 on Synology DSM):
[Intel Quick Sync](../hardware.md#intel-quick-sync-and-amd-vaapi) has the compose block. ARM models
have no Quick Sync path at all: the VA-API drivers ship in the amd64 image only.

## Memory and disk

The 4 GB limit in the compose file is what the measured runs used. The streaming assembler blends
one clip at a time, so render memory does not grow with clip count. 4K on NAS hardware is not
recommended.

Size `thumbnail_cache_max_size_mb` against your library, not by taste: too small and the next
overlapping memory re-downloads every preview, which on a NAS is the slow part. The budget per
picture is in the
[config reference](../../reference/config-reference.md#size-the-thumbnail-cache-by-your-library).

## What the NAS cannot do

- Hold the 30B reader. Use another machine or `reader: rules`. A configured model reader that
  cannot be reached stops the run; `no_captions` does not turn it off.
- Generate music: MusicGen and ACE-Step want GPU servers. Upload your own track instead.
- Title screens on the GPU kernels. The J4125 has no AVX, so the kernel renderer is out entirely
  and every title is PIL-rendered: [CPUs without AVX](../hardware.md#cpus-without-avx).

## NAS notes

Synology Container Manager, QNAP Container Station, the TrueNAS SCALE Apps system and the Unraid
Docker Compose Manager plugin all take the compose file above as a project.

For a nightly memory, set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
`IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` (plus `TZ`) in `environment:`. The UI process makes
the decision itself; there is no cron to install.
[Daily automation](../installation/docker.md#daily-automation).
