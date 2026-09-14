---
sidebar_label: "Linux + NVIDIA"
---

# Linux + NVIDIA GPU Setup

For Linux servers with NVIDIA GPUs. Docker with nvidia-container-toolkit for NVENC encoding, CUDA scaling, GPU title rendering, and optional AI music generation.

Complete [editorial annotation setup](../configuration/editorial-preparation.md) before the
first uncached generation. The tier decides whether classifiers or captions are required; the rules reader needs no
language model. These choices are separate from NVENC and music.

## Who this is for

This setup runs the app on a Linux server with an NVIDIA GPU that supports the required NVENC
codec. Install a compatible driver and follow the [NVIDIA Container Toolkit instructions](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
for your distribution. The vendor guide also covers configuring Docker and testing GPU access.

## Docker Compose

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"        # loopback only: see below to reach it remotely
    volumes:
      - immich-memories-config:/home/immich/.immich-memories
      - immich-memories-library-cache:/home/immich/.cache
      - immich-memories-scratch:/tmp
      - ./output:/app/output          # mkdir + chown to the container UID first, see below
    environment:
      IMMICH_URL: "${IMMICH_URL}"
      IMMICH_API_KEY: "${IMMICH_API_KEY}"
      IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER: "no_captions"
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
  immich-memories-library-cache:
  immich-memories-scratch:
```

The port is published on loopback only. A GPU box is usually headless, so either tunnel
(`ssh -L 8080:localhost:8080 your-server`) or publish it properly: enable
[authentication](../configuration/authentication) first (the app holds an Immich API key to your
whole photo library), then change the mapping to `"8080:8080"`.

One thing the compose file cannot do for you:

- **Bind-mount ownership**: the container runs as UID/GID 1000. Create the output directory
  yourself (`mkdir -p output`; `chown 1000:1000 output` if your user isn't 1000) or Docker
  creates it as root and the app cannot write there. See
  [Docker install](../installation/docker.md) for the alternatives.

## .env file

```bash
IMMICH_URL=http://your-immich-server:2283
IMMICH_API_KEY=your-api-key-here
```

## What works

- **NVENC encoding**: hardware-accelerated H.264/H.265 encoding. NVIDIA is probed first, so NVENC is used automatically: nothing to configure.
- **GPU title renderer**: full particle effects and gradient backgrounds using the NVIDIA GPU.
- **AI music generation**: if you run a MusicGen or ACE-Step server alongside, configure it in the `musicgen` or `ace_step` config sections.
- **Every memory type**: all ten, same as anywhere else. What the card accelerates is the encode, the scaling and the titles. Preparation, the heads and the detectors are CPU work here, and the two model services are their own problem.

## What doesn't work

- **The reader on a small card**: the graded reader is 30B parameters at 4 bits, so roughly 17 GB of weights must fit while it is loaded, plus context and runtime overhead. A 24 GB card (3090, 4090) holds that; what a smaller one does about the overflow is up to whichever serving stack you pick, and nobody has measured it here. Use a remote reader or `reader: rules` if it does not fit.
- **A graded NVIDIA configuration**: there isn't one. The approved matrix ran on Apple Silicon MLX, for both the reader and the captions. Any OpenAI-compatible vision model with a 32k context is expected to work here; nobody has compared its output to the graded run.

## First run and sizing

```bash
docker compose exec immich-memories immich-memories models fetch
docker compose exec immich-memories immich-memories preflight
```

The example uses the rules reader and `no_captions`. Configure a reader separately below, or
use `metadata_only` to skip classifiers. The 8 GB memory limit is a starting budget, not a
measurement for every workload. The [running-mode measurements](../running-modes.md) distinguish
selection from rendering; measure peak RAM and VRAM on your own media.

## Pointing the reader at this box

The reader groups the period's days into stories, weighs them and picks the pictures, and it is
sent an 800 px tile of the candidates whose facts the edit demands, a few dozen per memory, so
this seat needs vision and at least a 32k context. The
one graded configuration is `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX, which is
Apple Silicon only. On NVIDIA, serve an equivalent vision model with vLLM or Ollama and treat the
quality as your own measurement.

Whatever you run, it has to answer `/v1/chat/completions` with images and honour
`response_format: json_schema`. Then point the app at it:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://your-model-host:8000/v1
    model: the-tag-your-server-reports
```

`model` has to be what the server reports at `/v1/models`, exactly. Other vision models must satisfy the same contract; their quality needs testing on your media.

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
- **VRAM monitoring**: watch `nvidia-smi` during generation. Nobody has recorded peak VRAM for the encode or for GPU titles, so measure your own before you size a card around them.
- **Headless Linux**: the CLI works fully on headless servers. Use `immich-memories generate` instead of the UI if you don't need a browser.
