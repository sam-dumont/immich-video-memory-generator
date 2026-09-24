---
title: Requirements and tiers
---

# Requirements and tiers

Reader: newcomer and power user.

The default install is one container on the box that already runs Immich. It works on a plain
NAS and makes the whole film there; a GPU or a model makes it better, and both are optional.

## Hardware

For this app's container, on top of what Immich itself uses:

| | Minimum | Recommended |
|---|---|---|
| RAM | 4 GB free for the container (the compose file's limit) | 8 GB, for 4K output or a render running beside Immich's own jobs |
| CPU | 2 cores, x86-64 or ARM64 | 4 cores, x86-64 with AVX |
| Disk | 25 GB on the config volume, plus the 2.4 GB image and your films | the config volume on an SSD |
| OS | Linux with Docker Engine and Compose v2 | same; Docker Desktop on a Mac or Windows works too |
| Immich | v2 or v3, and an API key | same |

What the minimum costs you:

- **Two cores** make the render the long part of every run. The editor banks what it reads, but not
  the encode: a second cut of the same month reads nothing again and still encodes the whole
  film.
- **No AVX** (Intel Celeron J4125 and friends) means the CPU fallback draws the titles instead
  of the animated title kernels: [CPUs without AVX](./hardware.md#cpus-without-avx).
- **ARM64** gets no hardware encoder: the VA-API drivers ship in the amd64 image only.

The 25 GB is the caches at their default budgets: 10 GB of Immich previews, 10 GB of downloaded
video kept 7 days, 2 GB of clip previews. The models are about 130 MB. The one file worth backing
up is `annotations.sqlite`, where every fact the editor read is banked:
[What to keep](./docker.md#what-to-keep).

Tested on a Synology DS423+ (Celeron J4125, four cores), on an Apple Silicon Mac and on a
Kubernetes cluster. Timings per host are on [Measured](../better/measured.md).

## The three setups

| Setup | What you run | What it adds |
|---|---|---|
| **A plain NAS** (the default) | This container and one `models fetch` | The film: the rules editor, eight context heads and two detectors on every picture the film can reach, the family-viewing gate, titles, maps, music |
| **+ a GPU box** (optional) | The [inference service](../better/inference.md) or the [render worker](../better/gpu-render.md) on an NVIDIA box | The same facts, faster, or the encode off the NAS. Nothing already banked is read again |
| **+ a model** (optional) | A model with a 32k context; it reads text only, so it needs no vision. About 17 GB resident for the 30B one at 4-bit, on a 32 GB Mac or a 24 GB card, or a hosted API key | A reader that writes the prose (what happened, the title) and polishes the draft the rules editor makes, free-text subjects, and captions if you also raise the tier. [What a model adds](../better/overview.md) |

With a model, the rules editor still makes the draft. The model reads it and says which shots add
nothing, so the NAS setup is the same editor minus that last pass.

## The preparation tier

`advanced.editorial.preparation.tier` decides which producers run on each picture before the edit.

| Tier | What runs | What the family-viewing gate can do |
|---|---|---|
| `metadata_only` | Previews and pixel measurements. Nothing to download | Nothing looked at the pictures, so nothing is cleared: every picture stays family-only, and a `sendable` export is refused |
| **`no_captions`** (the NAS tier) | The above, plus the DINOv2 encoder with its eight heads and the two detectors. Needs `models fetch` | Refuses what `full` refuses. Never clears a picture, because eight findings are only named by a sentence |
| `full` | All of the above, plus one caption per picture from a 500M vision model | Everything, including clearing a picture for a `sendable` export. Needs a [caption server](../better/captions.md) |

A cut prepares only what it can reach: the pictures the film can select, their Live Photo clips
and the bursts around them, not the whole date window. `immich-memories prepare` reads a whole
scope ahead of time, if you want the first cut to be quick.

Everything is banked per picture and per producer, so changing the tier erases nothing, and a
`no_captions` library can add captions later, a month at a time.

### Which tier you get

The code default is `full`. A config that names no `llm.model` and no caption endpoint settles to
`no_captions` on its own and logs it once, and the shipped `docker-compose.yml` pins `no_captions`
anyway, so a first `up` needs no second service.

The trap is the other way round: on an install that does not pin the tier, setting only
`IMMICH_MEMORIES_LLM__MODEL` flips it back to `full`, which then wants a caption server. Pin
`tier: no_captions` when you add a reader and don't want captions.

`immich-memories preflight` follows the tier: `metadata_only` skips the model files, `no_captions`
skips the caption server.
