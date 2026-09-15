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

### Do not cap the CPU with `cpus:` on a Synology

Docker's `cpus:` (and `--cpus` on the command line) is a CFS quota, and DSM runs a cgroup v1
kernel built without the CFS bandwidth controller. A DS423+ answered `docker compose up` with
`NanoCPUs can not be set, as your kernel does not support CPU CFS scheduler or the cgroup is not
mounted` and started nothing at all. `cpuset: "0-3"` pins the same four cores and works there;
the shipped `docker-compose.yml` sets no CPU limit for that reason. Memory limits are fine on
every NAS tested.

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
docker compose exec immich-memories immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

`models fetch` writes the two digest-pinned ONNX exports (the 88 MB DINOv2-small encoder and the
22.5 MB sensitive-content detector) under `~/.immich-memories/models`, which is the config volume,
and warms the document classifier's Hugging Face snapshot. That third one goes to
`/home/immich/.cache/huggingface` unless you say otherwise, which is the container's writable layer
and is gone on the next `docker compose pull`. Add
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR: "/home/immich/.immich-memories/models/huggingface"`
to `environment:` before you fetch and all three land on the volume. All three run on the CPU. A producer that cannot load its model says so by name in the
first seconds of the detector stage, and names the command that fixes it.

The run prints one line per cut with the per-producer cost
(`preparation tier=no_captions: 1234 pictures requested; detectors 0.396s/pic ...`), so
`docker compose logs immich-memories | grep "preparation tier"` tells you which producer a box
cannot afford. Run the same month twice: the difference is the cold cost.

### What the first run costs

Plan an overnight for the first pass over a big month, and seconds for every pass after it.

<!-- Fields in output/setup-matrix/demo/run1/summary.data.json, cell nas-rules-local:
     1.4404 prepared.seconds_per_picture, 0.5963 and 0.6930 prepared.producers[].seconds_per_picture
     for public_heads and detectors, 1,483 s timing.render_s, 54.0 s video.duration_s,
     180 s and 120 s timing.prepare_cold_s for nas-rules-local and nas-rules-service,
     0 s timing.prepare_warm_s, 5 s timing.selection_s. 13,552 is prepared.pictures in
     output/setup-matrix/february/run2/summary.data.json. -->

The setup matrix measured this box again in September 2026, on the 133-picture demo month at
`no_captions`: 1.4404 s per picture cold, 0.5963 s of it the encoder and its six heads and 0.6930 s
the two detectors. That is the same work as the 1.23 s per picture in the tier table above, on a
different month and a different run: two single observations a fifth apart, not a change in the
code. Plan on the slower one. Then 1,483 s to render a 54-second film on the four cores. A 13,552-picture
month is 19,520 s of preparation at that rate, so about 5 h 25 min before the render starts. That
5 h 25 min is a rate multiplied by a count: the NAS lane on a real month was set up and stopped
before it ran, so nobody has waited through it. The second run over the same month prepares in 0 s
and selects in 5 s.

The render is the part a warm cache does not help. Two ways out, neither required: move the picture
facts to a GPU box with the [inference service](../installation/inference-service.md), which took
this NAS from 180 s to 120 s of cold preparation on the demo month, 1.4404 s a picture down to
1.0865, and
[#931](https://github.com/sam-dumont/immich-video-memory-generator/issues/931), a render worker
beside that service so the NAS stops encoding on its own CPU. The render worker is not built yet.
[Running modes](../running-modes.md#what-to-expect-on-a-first-run) has the same month on a Mac and
on a Kubernetes cluster.

### A reader you do not host

If there is no second machine, the third option is a provider. It is the same `model` reader, over
the same contract, and it costs the privacy trade on [Network & Privacy](../configuration/network-and-privacy.md):
800 px tiles of the few dozen candidates the edit asks about, plus their annotation lines with
people and place names, leave your network.

```yaml
      IMMICH_MEMORIES_LLM__PROVIDER: "openai"      # ollama | openai-compatible | openai | zai | anthropic
      IMMICH_MEMORIES_LLM__MODEL: "gpt-4.1-mini"
      IMMICH_MEMORIES_LLM__API_KEY: "${OPENAI_API_KEY}"
```

Leave `llm.base_url` unset and the provider name fills in its own URL: `openai` takes
`https://api.openai.com/v1`, `anthropic` takes `https://api.anthropic.com` and its Messages API,
`zai` takes `https://api.z.ai/api/anthropic`. Any other host that speaks one of those two dialects
takes `openai-compatible` (or `anthropic`) plus a `base_url` of your own, which is how a Melious
endpoint is configured. The model has to take images and hold a 32k context either way.

On a real month, ten cells were timed: 8 s for no model at all, 13 min 45 s to 1 h 53 min for the
hosted ones, 19 min for the local 30B. Costs ran from EUR 0.054 to EUR 0.585 of tokens at list
price, and three cells stopped before producing a cut. What each one did:
[Readers](../readers.md). Only monthly memories were measured; years and trips were not.

### Adding captions later

The shipped `docker-compose.yml` carries a caption server behind a profile, so a NAS with the
patience for it needs no second machine:

```bash
docker compose --profile captioner up -d
curl -s localhost:8092/v1/models
```

`IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_CONCURRENCY` already defaults to 1, which is what
this box wants. On four Celeron cores a caption is 30.9 s at 1, and four at once is slower, not
faster: four image encodes share the threads of one. At that rate captioning a 13,552-picture month
is four days, which is the whole reason this page recommends `no_captions`.
[Caption server](../installation/caption-server.md) has the flags and what each one costs when it is
missing.

Or point the container at a caption endpoint on another box and run the same scope again on the
`full` tier:

```bash
docker compose exec \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=full \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL=http://model-box.lan:8092/v1 \
  immich-memories immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

About 2,700 pictures a day on a DS423+. Do it a month at a time, overnight, then set the compose
tier to `full` once you are caught up. There is no background backfill job.

## Encoding on an Intel NAS

Intel Gemini Lake (the J4125 class) has an H.264 encode entrypoint and no HEVC one, and the
default output codec is H.265. The backend encodes what the chip can and sends the rest to
libx265, with `vaapi cannot encode h265 on this device; encoding it in software` in the log. So
set `output.codec: h264`, or turn on `preset: fast`, and pass the device through:

```yaml
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "937"        # the GID that owns /dev/dri/renderD128 on YOUR host
```

The render node is usually `root:render` with no world access, so `devices:` alone leaves it
present and unopenable. The GID differs per host: 104 on Debian, 937 on Synology DSM;
`stat -c '%g' /dev/dri/renderD128` prints yours. ARM NAS models have no Quick Sync path; the
VA-API drivers ship in the amd64 image only. Details on [Intel Quick Sync](../hardware.md#intel-quick-sync-and-amd-vaapi).

## One switch: `preset: fast`

`IMMICH_MEMORIES_PRESET=fast` in the compose `environment:` (or `preset: fast` at the top of
`config.yaml`) sets 1080p H.264 at the fast quality tier with the fast encoder preset and static
title backgrounds. Five keys, nothing about what the editor reads. Any value you set explicitly
still wins, and the Generation Options page shows a banner while the preset is active.

## Memory and disk

The 4 GB limit in the compose file is the one the measured runs used. The streaming assembler
blends one clip at a time, so render memory does not grow with clip count. 4K on NAS hardware is
not recommended; raise the limit if you try.

The video cache keeps downloaded Immich clips (10 GB, 7 days by default). The preview cache is the
one to size by library: one Immich preview is about 315 KB, so budget
`thumbnail_cache_max_size_mb ≈ 0.35 × pictures a memory's scope can reach`; the 10 GB default
covers about 31,000 previews. Too small and the next overlapping memory re-downloads every preview
and re-captions the pictures whose banked caption failure no longer matches, which on a NAS is the
slow part. A run that does not fit logs one `WARNING` naming the setting.

## What the NAS cannot do

- Hold the 30B reader. Use another machine or `reader: rules`. A configured model reader that
  cannot be reached stops the run; `no_captions` does not turn it off.
- Generate music: MusicGen and ACE-Step want GPU servers. Upload your own track instead.
- Title screens on the GPU kernels: they fall back to the PIL renderer, which keeps the animated
  gradient and loses the particle effects and the SDF text. On a CPU with no AVX, the J4125 in the
  tested DS423+ included,
  the kernel renderer is out entirely: it dies with SIGILL on the first kernel it compiles, a
  child-process probe catches that at startup, and every title is PIL-rendered. See
  [CPUs without AVX](../hardware.md#cpus-without-avx).

## NAS notes

- **Synology**: Container Manager, create the project from the compose file above.
- **Unraid**: add it as a Docker container or use the Docker Compose Manager plugin.
- **TrueNAS SCALE**: the Apps system, or a custom compose.
- **QNAP**: Container Station with the compose file.
- **Scheduling**: `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
  `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` (plus `TZ`) in `environment:`. The UI process runs
  the daily decision itself; there is no cron to install. See [Daily automation](../installation/docker.md#daily-automation).
