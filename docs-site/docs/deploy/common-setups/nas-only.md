---
sidebar_label: "NAS + a model box"
---

# A NAS, and one machine that can hold the models

For Synology, QNAP, Unraid and TrueNAS users already running Immich on the box.

**A NAS on its own stopped being enough when the editorial engine landed.** The editor reads your
pictures before it cuts them, and reading them takes a vision model with roughly 17 GB of weights
resident plus a caption server. No Celeron holds that, and the app refuses to cut rather than guess
without it. What the NAS is still good at is everything else: talking to Immich over the LAN,
holding the caches, running the CPU detectors and heads, and encoding the video.

Read [Self-hosting: start here](../self-hosting.md) first. It is the whole stack in order; this
page is the NAS-shaped version of steps 1, 2 and 7.

## Who this is for

You have a NAS with Docker support (Synology DSM 7+, Unraid, TrueNAS SCALE, QNAP Container
Station) and Immich already on it, and you have one other machine on the network that can hold the
two model services: an Apple Silicon Mac with 32 GB, or a box with a 24 GB GPU. If you do not have
that second machine, this deployment is [unsupported](../self-hosting.md#one-machine-or-two), not
slow.

## What runs where

| Piece | Where | Why |
|---|---|---|
| The app, the web UI, the caches | NAS | Cheap. 2 to 4 GB resident |
| Immich | NAS | Already there |
| The vision reader (weighs the period, looks at the pictures the edit asks about) | The other machine | ~17 GB resident at 4-bit |
| The caption server (one description per picture, banked forever) | The other machine | MLX weights, so Apple Silicon today |
| The DINOv2-small ONNX encoder (88 MB) and six context heads | NAS | CPU inference, no GPU path |
| Two detector snapshots (~400 MB) | NAS | CPU only, both |
| Encoding, title screens, the render | NAS | See [encoding](#encoding-what-the-chip-will-and-will-not-do) below |

![NAS setup diagram](/img/diagrams/setup-nas.png)

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
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_BASE_URL: "http://model-box.lan:8092/v1"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

volumes:
  immich-memories-config:
```

`llm.model` has to be the exact string the reader reports at `GET /v1/models`. The caption endpoint
has to advertise the alias `smolvlm2-500m-base-public` at `/models`; the client checks the
inventory and three schema controls before it sends a single preview.
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
snapshots into the Hugging Face cache on the config volume. Both run on the CPU. With them cached,
`allow_model_downloads` stays `false` and means it. `immich-memories preflight` checks Immich, the
reader, the encoder digest and the caption alias; it does **not** check the detector snapshots, so
the first cut is where a cold cache shows up, with a count per missing producer.

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

- **Hold the reader or the caption server.** That is the whole reason for the second machine. A
  missing provider stops an uncached run with an explicit incomplete result; there is no model-free
  alternate selector to fall back to.
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

### Preparation, not measured yet

Preparation reads every candidate asset in the memory's scope exactly once: an Immich preview
fetched, pixel measurements, six heads, two detectors, one caption. Nobody has timed that on
NAS-class silicon yet, and a benchmark is running now. A number will land here when it exists.
Until then, what is known:

- it is a per-candidate cost, not a per-selected-clip cost, so it scales with how wide your date
  range is rather than with the length of the video;
- it is paid once. Every producer banks its answer by exact input, so the second cut over the same
  period, and any overlapping memory, skips the work entirely;
- the CPU half of it (heads, detectors, pixels) is what the NAS is doing; the caption and the
  period reading happen on the other machine.

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
