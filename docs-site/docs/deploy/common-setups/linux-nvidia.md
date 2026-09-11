---
sidebar_label: "Linux + NVIDIA"
---

# Linux + NVIDIA GPU Setup

For Linux servers with NVIDIA GPUs. Docker with nvidia-container-toolkit for NVENC encoding, CUDA scene analysis, GPU title rendering, and optional AI music generation.

Complete [editorial annotation setup](../configuration/editorial-preparation.md) before the
first uncached generation. The pinned public context encoder, detector weights, compact-caption
endpoint and story model are separate requirements from NVENC and the music backends below.

## Who this is for

You have a Linux server (Ubuntu, Debian, Fedora) with an NVIDIA GPU (GTX 1050 or newer — Pascal is where NVENC starts). You want hardware-accelerated encoding and optionally want to run MusicGen or ACE-Step for AI-generated background music.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ Linux Server (NVIDIA GPU)                            │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Docker (nvidia-container-toolkit)              │  │
│  │                                                │  │
│  │  ┌──────────────────┐  ┌────────────────────┐ │  │
│  │  │ Immich Memories   │  │  MusicGen API      │ │  │
│  │  │ NVENC encoding   │  │  (optional)        │ │  │
│  │  │ CUDA analysis    │  │  port 8000         │ │  │
│  │  │ port 8080        │  │                    │ │  │
│  │  └──────────────────┘  └────────────────────┘ │  │
│  └────────────────────────────────────────────────┘  │
│                     │                                 │
│            ┌────────┴─────────┐                       │
│            │  Immich server   │                       │
│            └──────────────────┘                       │
└──────────────────────────────────────────────────────┘
```

![Linux setup diagram](/img/diagrams/setup-linux.png)

## Prerequisites

Install the NVIDIA container toolkit:

```bash
# Ubuntu/Debian
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify with: `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only — see below to reach it remotely
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - ./output:/app/output          # mkdir + chown to the container UID first, see below
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      NVIDIA_DRIVER_CAPABILITIES: compute,video,utility   # `video` = NVENC/NVDEC libraries
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu, video]
        limits:
          memory: 8G

volumes:
  immich-memories-config:
```

The port is published on loopback only. A GPU box is usually headless, so either tunnel
(`ssh -L 8080:localhost:8080 your-server`) or publish it properly: enable
[authentication](../configuration/authentication) first — the app holds an Immich API key to your
whole photo library — then change the mapping to `"8080:8080"`.

One thing the compose file cannot do for you:

- **Bind-mount ownership**: the container runs as UID/GID 1000. Create the output directory
  yourself (`mkdir -p output`; `chown 1000:1000 output` if your user isn't 1000) or Docker
  creates it as root and the app cannot write there. See
  [Docker install](../installation/docker.md) for the alternatives.

## .env file

```bash
IMMICH_URL=http://immich-server:2283
IMMICH_API_KEY=your-api-key-here
```

## What works

- **NVENC encoding**: hardware-accelerated H.264/H.265 encoding. NVIDIA is probed first, so NVENC is used automatically — nothing to configure.
- **CUDA scene analysis**: frame differencing for scene detection runs on the GPU when OpenCV has CUDA support. Face detection is CPU (OpenCV Haar cascades) on Linux — there is no CUDA face path.
- **Taichi GPU title renderer**: full particle effects and gradient backgrounds using the NVIDIA GPU.
- **AI music generation**: if you run a MusicGen or ACE-Step server alongside, configure it in the `musicgen` or `ace_step` config sections.
- **All memory types and features**: everything works with GPU acceleration.

## What doesn't work

- **The reader on a small card**: the graded reader is ~17 GB of 4-bit weights and they stay resident for as long as the server is up. A 12 GB card offloads the rest to system RAM and runs slowly; 24 GB (3090, 4090) holds it. Below that, point `llm.base_url` at a box that can — there is no cut without a reader.
- **A graded NVIDIA configuration**: there isn't one. The approved matrix ran on Apple Silicon MLX, for both the reader and the captions. Any OpenAI-compatible vision model with a 32k context is expected to work here; nobody has compared its output to the graded run.

## Performance expectations

No GPU run of this pipeline has been measured end to end, so there is no table here. The one
measured run is CPU-only ([NAS-only](./nas-only.md#performance-expectations)): 10 minutes for a
14-clip monthly, of which analysis was 7.4.

That shape is what to plan around. Render was 2.7 of those ten minutes and the encode is only part
of it — the titles are the rest, and a CUDA Taichi backend is what shortens those. The other 7.4
minutes are analysis and selection: downloading each candidate clip from Immich, scoring it, and
the LLM passes if you turned them on. Those are bounded by your Immich server and your LLM, not by
the card. Analysis is cached, so a second run of the same period is much cheaper than the first.

If you measure a run on your own box, [an issue](https://github.com/sam-dumont/immich-video-memory-generator/issues)
with the numbers is welcome.

## Pointing the reader at this box

The reader groups the period's days into stories, weighs them and picks the pictures — and it is
sent an 800 px tile of every candidate, so this seat needs vision and at least a 32k context. The
one graded configuration is `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX, which is
Apple Silicon only. On NVIDIA, serve an equivalent vision model with vLLM or Ollama and treat the
quality as your own measurement. The older Qwen3.6 pair was exercised against the retired per-clip
scorer, not this route.

Whatever you run, it has to answer `/v1/chat/completions` with images and honour
`response_format: json_schema`. Then point the app at it:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://your-model-host:8000/v1
    model: the-tag-your-server-reports
  content_analysis:
    enabled: true
```

`model` has to be what the server reports at `/v1/models`, exactly. Smaller vision models will
run; none of them has been graded on this route, so treat the output as your own experiment.

## Adding AI music

MusicGen or ACE-Step servers need their own GPU allocation. If you have a single GPU, time-share it: generate music first, then encode video. If you have multiple GPUs, dedicate one for music generation.

Configure in `config.yaml`:

```yaml
advanced:
  musicgen:
    enabled: true
    base_url: http://musicgen-server:8000
```

## Tips

- **Check GPU detection**: run `docker exec immich-memories immich-memories hardware` to verify GPU detection inside the container.
- **Multi-GPU**: pick the card with `NVIDIA_VISIBLE_DEVICES=0` (or `CUDA_VISIBLE_DEVICES`) in the container environment; there is no config knob for it.
- **VRAM monitoring**: watch `nvidia-smi` during generation. Peak VRAM usage is about 2-3 GB for encoding, 1-2 GB for Taichi title rendering.
- **Headless Linux**: the CLI works fully on headless servers. Use `immich-memories generate` instead of the UI if you don't need a browser.
