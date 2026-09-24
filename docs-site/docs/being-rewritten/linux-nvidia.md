---
sidebar_label: "Linux + NVIDIA"
unlisted: true
---

:::note[Being rewritten]

This page is being split into the new docs. Its text moves to [Inference service](../better/inference.md).

:::

# Linux + NVIDIA

A Linux box (Ubuntu, Debian, Fedora) with an NVIDIA card, running the app in Docker through the
nvidia-container-toolkit. The card does NVENC encoding, CUDA scaling and the GPU title kernels.
NVENC has shipped since Kepler, so almost any card of the last decade encodes; a GTX 1050 is a
safe floor.

What the card does not do is the reading. Work through
[editorial annotation setup](./editorial-preparation.md) before the first uncached
generation: the context encoder, the detector weights, the caption endpoint and the story model are
separate requirements from everything on this page.

![Linux setup diagram](/img/diagrams/setup-linux.png)

## Prerequisites

Install the NVIDIA container toolkit from NVIDIA's own
[installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html);
the per-distribution recipe moves, so it is not copied here. Whatever the guide says, it ends the
same way:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

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

With a `.env` beside it:

```bash
IMMICH_URL=http://immich-server:2283
IMMICH_API_KEY=your-api-key-here
```

A GPU box is usually headless, and the port above is published on loopback only, so either tunnel
(`ssh -L 8080:localhost:8080 your-server`) or publish it properly: enable
[authentication](../run/authentication) first (the app holds an Immich API key to your
whole photo library), then change the mapping to `"8080:8080"`.

The container runs as UID/GID 1000, so create the output directory yourself (`mkdir -p output`;
`chown 1000:1000 output` if your user isn't 1000) or Docker creates it as root and the app cannot
write there. The alternatives are on [Docker install](../run/docker.md).

## What the card is for

NVENC encoding, CUDA scaling and the GPU title renderer, all three probed and used automatically.
Preparation, the eight heads and the two detectors are CPU work here, and the reader and the caption
server are their own services. All ten memory types work, same as anywhere else.

## Pointing the reader at this box

The reader has to accept images and hold at least a 32k context: some candidates reach it as 800 px
tiles. The graded reader is 30B parameters at 4 bits, roughly 17 GB resident for as long as the
server is up. A 24 GB card (3090, 4090) holds that; below 24 GB, point `llm.base_url` at a box that
can, because there is no cut without a reader.

There is no graded NVIDIA configuration. The approved matrix ran the reader and the captions on
Apple Silicon MLX. Serve an equivalent vision model with vLLM or Ollama and treat the quality as
your own measurement. Ten readers were pointed at one real month and three of them stopped:
[Readers](../better/reader.md).

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://your-model-host:8000/v1
    model: the-tag-your-server-reports
```

`model` has to be what the server reports at `/v1/models`, exactly.

## Adding AI music

MusicGen and ACE-Step want their own GPU allocation. On a single card, time-share it: generate the
music first, then encode.

```yaml
advanced:
  musicgen:
    enabled: true
    base_url: http://musicgen-server:8000
```

## Checks worth running

`docker exec immich-memories immich-memories hardware` reports what the container sees, which is
the only view that matters: the host seeing the card proves nothing. On a multi-GPU box, pick one
with `NVIDIA_VISIBLE_DEVICES=0` in the container environment. Peak VRAM for the encode and for GPU
titles has never been recorded, so watch `nvidia-smi` before you size a card around them.

The setup matrix measured a Mac, a NAS and a Kubernetes cluster, not a bare-metal Linux box, so
there is no timing table for this page. What the card is worth against a CPU encode, measured on a
T1000, is on [Hardware](../run/hardware.md#what-the-card-is-actually-worth).
