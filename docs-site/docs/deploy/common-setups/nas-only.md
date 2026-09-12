---
sidebar_label: "NAS + a model box"
---

# A NAS, and one machine that can hold the models

For Synology, QNAP, Unraid and TrueNAS users already running Immich on the box.

**The NAS can make a cut on its own with the rules reader, or use a model on another machine.**
The model-assisted setup below uses a vision reader with roughly 17 GB of weights resident.
That model needs more memory than the tested DS423+ provides. The NAS handles Immich access,
caches, image preparation and encoding. The rules option skips the language-model editor and
makes a simpler cut from dates, places, favourites, people and available image facts.

**The caption server is no longer part of that list.** Set
[`editorial.preparation.tier: no_captions`](#preparation-tiers-what-the-nas-actually-pays) and the
NAS prepares every producer the audience gate reads and skips the one that costs 25 times the rest
put together. Measured on a DS423+: **3 h 41 min** for a 10,793-picture library instead of four
days.

Read [Self-hosting: start here](../self-hosting.md) first. It is the whole stack in order; this
page is the NAS-shaped version of steps 1, 2 and 7.

## Who this is for

You have a NAS with Docker support (Synology DSM 7+, Unraid, TrueNAS SCALE, QNAP Container
Station) and Immich already on it, and you have one other machine on the network that can hold the
two model services: an Apple Silicon Mac with 32 GB, or a box with a 24 GB GPU. If you do not have
that second machine, use the rules option below; the model-assisted setup needs a reachable
reader provider.

## NAS alone, without inference

```yaml
advanced:
  editorial:
    reader: rules
    preparation:
      tier: metadata_only
```

This supports the ten standard memory products, including albums and person memories.
Custom semantic subjects require a model reader. In the February benchmark on the DS423+,
selection took **279 seconds with cold pixel facts and 11.1 seconds on repeat**, with zero
model requests. Those times exclude rendering and music. The rules cut can omit occasions,
spend slots on mundane objects and lose model audience judgements; inspect it before sharing.

Use `no_captions` with `reader: rules` if you want image classifiers without a language-model
editor. A populated annotation store retains previously computed facts; changing tiers does
not erase them. See [editing without a language model](../configuration/editorial-preparation.md#editing-without-a-language-model)
for the evidence and capability limits.

## What runs where, with a model reader

| Piece | Where | Why |
|---|---|---|
| The app, the web UI, the caches | NAS | Cheap. 2 to 4 GB resident |
| Immich | NAS | Already there |
| The vision reader (weighs the period, looks at the pictures the edit asks about) | The other machine | ~17 GB resident at 4-bit |
| The caption server (one description per picture, banked forever) | Nobody, on the `no_captions` tier | 25× the cost of every other producer together. Optional, and backfillable later |
| The DINOv2-small ONNX encoder (88 MB) and six context heads | NAS | CPU inference, no GPU path |
| Two detector snapshots (~400 MB) | NAS | CPU only, both |
| Encoding, title screens, the render | NAS | See [encoding](#encoding-what-the-chip-will-and-will-not-do) below |

![NAS setup diagram](/img/diagrams/setup-nas.png)

## Preparation tiers: what the NAS actually pays

`editorial.preparation.tier` names which producers this deployment asks for. It is a named choice
and never a fallback: a producer the tier demands and cannot reach still stops the run with a count
per missing producer.

| `tier` | What runs | First pass, 10,793 pictures on a DS423+ (Celeron J4125) |
|---|---|---|
| `full` | pixels, encoder + six heads, both detectors, captions | 4 days |
| **`no_captions`** ← the NAS tier | pixels, encoder + six heads, both detectors | **3 h 41 min** |
| `metadata_only` | pixels and Immich metadata; no ONNX, no captions | minutes |

Measured per picture on that box: 1.23 s for all the producers together, of which the DINOv2 embed
is 0.53 s and the `nsfw_marqo` detector 0.40 s — against **30.9 s** for one caption. Peak resident
memory 570 MB. Preparation is banked per picture, so these are first-pass costs; the second cut over
the same period pays only a preview check.

**What `no_captions` costs you.** No descriptions, so the reason under each picture is the facts
that funded it rather than a sentence, and the Memory page says so in one line under the title.
`nsfw_marqo`, `swim`, `children`, `exposure` and `doc_docling` are all still produced, so the
audience gate refuses everything it would have refused.

What it will not do is **clear** a unit. Eight of the findings that hold a picture to the family —
bathing, toileting, intimate hygiene, a medical procedure, an identifying record and the rest — are
named only by a written description. A detector head cannot see them, so a head seeing nothing is
not a clearance, and the gate may only ever tighten. Every unit therefore stays at family viewing
with a finding that says the description was missing rather than unreadable. To earn a `sendable`
export, run the tier that does the reading.

**What `metadata_only` costs you.** Everything above, plus the six heads and both detectors — and
with them sensitive-content and document detection. Because the gate then has no evidence, that tier
holds **every** unit to family viewing and refuses a `sendable` export outright. Use it only on a
box that cannot run ONNX at all.

Captions are banked per picture, so a `no_captions` NAS can add them later at its own pace and move
to `full` when it is done. A first cut should not wait four days for a progress bar.

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only: see "Reaching the UI" below
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output          # create it first and chown to the container UID, see below
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      # The two model services, on the other machine. Not localhost:
      # inside a container localhost is the container.
      IMMICH_MEMORIES_LLM__BASE_URL: "http://model-box.lan:8000/v1"
      IMMICH_MEMORIES_LLM__MODEL: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
      # The NAS tier. Drop this line (and add the caption endpoint below) only
      # once you are willing to pay four days for the first pass.
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"
      # Needed on the `full` tier only:
      # IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://model-box.lan:8092/v1"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

volumes:
  immich-memories-config:
```

`llm.model` has to be the exact string the reader reports at `GET /v1/models`. On the `full` tier
the caption endpoint also has to advertise the alias `smolvlm2-500m-base-public` at `/models`; the
client checks the inventory and three schema controls before it sends a single preview. On
`no_captions` no caption endpoint is contacted at all.
[Editorial annotation setup](../configuration/editorial-preparation.md) has every pin and digest.

### Reaching the UI

The mapping above is loopback-only, so on a headless NAS nothing reaches the UI until you do one
of two things. The cheap one is an SSH tunnel: `ssh -L 8080:localhost:8080 your-nas`, then open
`http://localhost:8080` on your desktop. Nothing is published and there is nothing to secure.

To publish it on the LAN instead, do both halves: turn on
[authentication](../configuration/authentication) first (the app holds an Immich API key to your
whole photo library), then change the mapping to `"8080:8080"`.

One more thing to get right before the first run:

- **Ownership of `./output`**: the image already writes to `/app/output`, so the mount above is
  where the videos land. The container runs as UID/GID 1000. Create the folder yourself
  (`mkdir -p output`) so Docker doesn't create it as root; if your NAS user isn't 1000,
  `chown 1000:1000 output`.
  On Synology/QNAP where that is awkward, use a named volume (`immich-memories-output:/app/output`)
  and `docker cp` the finished video out, or turn on upload-back to Immich and fetch it there.

## .env file

```bash
IMMICH_URL=http://immich-server:2283
IMMICH_API_KEY=your-api-key-here
```

If Immich runs on the same Docker network, use the container name (`immich-server`). If it's on a different machine or behind a reverse proxy, use the full URL (`https://photos.example.com`).

## Fetch the model files the NAS does own

```bash
docker compose exec immich-memories immich-memories models fetch
```

That writes the pinned DINOv2-small ONNX export, digest-checked, and warms the two detector
snapshots into the Hugging Face cache on the config volume. Both run on the CPU, both are in the
published image's dependency set, and together they are everything the `no_captions` tier needs.
With them cached, `allow_model_downloads` stays `false` and means it. `immich-memories preflight`
checks Immich, the reader, the encoder digest and the caption alias; it does **not** check the
detector snapshots, so the first cut is where a cold cache shows up, with a count per missing
producer.

## The first run on a NAS, start to finish

Everything below runs on the NAS itself, from the published image. Start with **one month**, not a
year: preparation scales with how wide the date range is, not with how long the video is.

```bash
# 1. Bring the stack up and confirm the tier the container actually resolved.
docker compose up -d
docker compose exec immich-memories \
  python -c "from immich_memories.config_loader import Config; print(Config().editorial.preparation.tier)"
# -> no_captions

# 2. Fetch the encoder and both detector snapshots once.
docker compose exec immich-memories immich-memories models fetch

# 3. Check Immich and the reader are reachable before paying for anything.
docker compose exec immich-memories immich-memories preflight

# 4. One month, cold. This is the run that measures the box.
docker compose exec immich-memories \
  immich-memories generate --type monthly --duration 60

# 5. The per-producer numbers that run just took, on this machine.
docker compose exec immich-memories sh -c \
  'ls -t /home/immich/.immich-memories/cache/editorial-runs/*/attempts/*/preparation.private.json \
   | head -1 | xargs cat'
```

The run prints the same thing as it goes — one line per cut,
`preparation tier=no_captions: 1234 pictures requested; detectors 0.396s/pic ...` — so
`docker compose logs immich-memories | grep "preparation tier"` works too, and the end-of-run block
names the tier under `TIER`. A wall-clock total cannot tell you which producer a box cannot afford;
these numbers can.

A second `generate` over the same month should skip preparation almost entirely — every producer is
banked by exact input — so run it twice and the difference is the cold cost.

### Adding captions later

Captions are banked per picture, so nothing is lost by starting without them. When you want them,
point the container at a caption endpoint and run the same scope again at the `full` tier:

```bash
docker compose exec \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=full \
  -e IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL=http://model-box.lan:8092/v1 \
  immich-memories immich-memories generate --type monthly --duration 60
```

That run pays the caption cost once for that scope — about 2,700 pictures a day on a DS423+, so a
10,793-picture library is roughly four days — and every later cut over those pictures reads the
banked descriptions for free. Do it a month at a time, overnight, in whatever order you care about,
then flip the compose `TIER` to `full` for good once you are caught up.

There is no background backfill job yet; this is the supported way to do it today.

## What the NAS does well

- **Preparation that is not the models**: six context heads over the ONNX encoder, two detectors,
  and the pixel measurements. All CPU, all banked by producer and exact input, so a second cut over
  the same period skips them entirely.
- **Title screens**: the PIL renderer, everywhere, no GPU needed.
- **Custom music**: upload your own MP3/WAV on the Options page.
- **All memory types**: year in review, monthly, person spotlight, trips (if GPS data exists).
- **Scheduling**: set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
  `IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00` (plus `TZ`) in the compose `environment:`. The UI
  process runs the daily decision itself, so there is no cron to install. `immich-memories auto
  install` is for host installs and cannot write a cron job inside the container; to drive it from
  the NAS host's scheduler instead, use
  `docker exec immich-memories immich-memories auto run --quiet --cooldown 24` and leave the
  built-in timer off. See [Daily automation](../installation/docker.md#daily-automation).
- **Photo support**: Ken Burns animations, face-aware pan, blur backgrounds.

## What it cannot do

- **Hold the tested 30B reader.** Use another machine for that model, or choose `reader: rules`.
  `no_captions` changes preparation; it does not disable a configured model reader. An explicitly
  selected model reader still stops if its provider is unavailable.
- **Hold the caption server** — but on `no_captions` it does not need to, and nothing asks for one.
- **AI music generation**: MusicGen and ACE-Step want GPU servers. Upload your own music instead.
- **The Taichi title renderer**: it falls back to PIL. Titles still look right, without the
  particle effects and animated gradients.

## Encoding: what the chip will and will not do

The old version of this page said NAS chips have no usable hardware encoder. That is wrong on
Intel silicon. On Gemini Lake (the J4125 class in a lot of Synology and mini-PC boxes) `vainfo`
lists an H.264 encode entrypoint and no HEVC one, and the project's default output codec is H.265.
The backend handles the split: it encodes what the device can and sends the rest to libx265, with
`vaapi cannot encode h265 on this device; encoding it in software` in the log.

So on an Intel NAS, set `output.codec: h264` (or turn on `preset: fast`, which sets it for you) and
the whole encode runs on the iGPU. Passing the device through takes two things, not one:

```yaml
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "937"        # the GID that owns /dev/dri/renderD128 on YOUR host
```

The container runs as uid 1000 and the render node is usually `root:render` with no world access,
so without `group_add` the device is present and unopenable. The render GID differs per host: 104
on Debian, 937 on Synology DSM. `stat -c '%g' /dev/dri/renderD128` on the host prints yours. Full
detail on [Intel Quick Sync](../hardware/intel-qsv.md). ARM-based NAS models have no QSV path; the
VA-API drivers ship in the amd64 image only.

## One switch: `preset: fast`

Add `IMMICH_MEMORIES_PRESET=fast` to the compose `environment:` (or `preset: fast` at the top of
`config.yaml`) and the CPU-friendly render profile is on: 1080p H.264 at medium quality with the
fast encoder preset, and static title backgrounds instead of animated ones. That is the whole of
it: three sections, five keys, nothing about what the editor reads. Every value you set explicitly
still wins, and the web UI's options page shows a banner when the preset is active.
`immich-memories --preset fast generate …` does the same for one CLI run.

```yaml
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      IMMICH_MEMORIES_PRESET: "fast"
```

## Performance expectations

### The render, measured

One measured run (2026-08-18): a monthly memory from a real library, 14 clips (7 videos + 6 photos,
HDR iPhone sources), 62 s of 1080p H.264 out, cold cache, the Docker image with `--cpus=4
--memory=4g` and no GPU (4 cores of an Apple M5 Max running the linux/arm64 image):

| Profile | Wall time | Analysis | Render | Output |
|---------|-----------|----------|--------|--------|
| `preset: fast` | 10 min 08 s | 7.4 min | 2.7 min | 30 MB |
| default | 15 min 42 s | 10.1 min | 5.6 min | 87 MB |

**Only the Render column still describes this product.** That run used the retired per-clip
scorer, so its Analysis column measures work the app no longer does. Read the table for the
encode and the title screens, nothing else.

### Preparation, measured

On a Synology DS423+ (Intel Celeron J4125, four cores, SSE4.2 and nothing above it), against a
10,793-picture scope, quiesced:

| Producer | Seconds per picture |
|---|---:|
| pixel facts | 0.055 |
| thumbnail hash | 0.020 |
| caption tile | 0.050 |
| DINOv2 preprocess | 0.041 |
| DINOv2 embed (ONNX) | 0.526 |
| six context heads | 0.018 |
| `nsfw_marqo` | 0.396 |
| `doc_docling` | 0.127 |
| **everything above** | **1.23** |
| one caption (GGUF, cold) | **30.9** |

Which is 21 minutes per 1,000 pictures for the producers and 8 h 55 min per 1,000 with captions:
**3 h 41 min against four days** for that library. Peak resident memory 570 MB. That one line is
the whole argument for the `no_captions` tier.

Two things that stay true whatever the tier:

- it is a per-candidate cost, not a per-selected-clip cost, so it scales with how wide your date
  range is rather than with the length of the video;
- it is paid once. Every producer banks its answer by exact input, so the second cut over the same
  period, and any overlapping memory, skips the work entirely.

The steady state is not the first pass. A family adding 50 pictures a day needs 25 minutes a day of
NAS captioning, which is nothing. The first pass is the problem, not the rate — which is why
captions are opt-in rather than a four-day wait before anybody sees a video.

Start with one month, not a year.

### Memory and disk

The 4 GB limit in the compose file is the one the measured run above used. The streaming assembler
blends one clip at a time into a single FFmpeg pipe, so render memory does not grow with clip
count; encoding 4K on NAS hardware is not recommended, and if you try it, raise the limit.

- Keep the video cache enabled (default). It holds downloaded Immich clips locally, so repeat runs
  skip the download. Defaults: 10 GB, files older than 7 days evicted.
- **Budget disk for the preview cache by library size, not by taste.**
  `thumbnail_cache_max_size_mb` holds one Immich preview per candidate asset a memory can reach.
  Measured on a real library, one preview is about 315 KB, so a 10,793-candidate scope wants around
  3.4 GB. Rule of thumb: `0.35 × assets in scope`, in MB. The 10 GB default covers roughly 31,000
  previews. Set it too low and the next overlapping memory re-downloads every preview over your LAN
  and re-captions the assets whose banked caption failure no longer matches, which on NAS-class
  hardware is the slow part. A run that does not fit logs one `WARNING` naming the setting.

If the render column is what you want to shrink, start with the title screens rather than the
encoder: see [title rendering is the bottleneck](../hardware/cpu-only.md#title-rendering-is-the-bottleneck-not-encoding).

## Tips for NAS users

- **Synology**: use Container Manager (formerly Docker). Create the project from the compose file above.
- **Unraid**: add as a Docker container in the Unraid UI or use Docker Compose Manager plugin.
- **TrueNAS SCALE**: use the built-in Apps system or deploy via custom Docker compose.
- **QNAP**: use Container Station with the compose file.
