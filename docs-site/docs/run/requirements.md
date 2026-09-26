---
title: Requirements and tiers
---

# Requirements and tiers

Reader: newcomer and power user.

The default install is one container on the box that already runs Immich. It works on a plain
NAS and makes the whole film there. That is a good default. GPU models and an LLM can add
some refinement; compare the result and decide whether it is worth the extra work.

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
| **+ GPU inference** (optional) | The [inference service](../better/inference.md) reporting CUDA, or a local CUDA/MLX runtime; the caption service and Laya must also be ready | The GPU tier adds captions and Laya for selected shots and replacement candidates. Existing captions stay banked |
| **+ an LLM** (optional) | A text model with a 32k context, such as local Gemma 4 E4B | Titles and other text features on every tier. With GPU inference too, Full adds prose and refinement of the NAS draft. [What a model adds](../better/overview.md) |

On Full, the rules editor still makes the draft. A preference vote keeps the original shot until
a replacement passes the checks. Sharing decisions stay with rules, classifiers and Laya.

A [render worker](../better/gpu-render.md) moves the encode to another machine. It does not change
the selection tier: a GPU that can encode video is not proof of inference capability.

## The preparation tier

Preparation and selection use the same product tier. Leave `tier` unset or use `tier: auto`.

| Tier | What runs | What the family-viewing gate can do |
|---|---|---|
| **`nas`** | Immich metadata, pixels, the DINOv2 encoder with eight heads and two detectors. Needs `models fetch` | Rules and classifiers check the pictures without a caption or prose LLM |
| `gpu` | NAS plus captions and Laya for selected shots and candidates. Needs a [caption server](../better/captions.md) and Laya | Laya may add holds; it cannot lift detector or rule holds |
| `full` | GPU plus the configured prose LLM | The same sharing checks as GPU; the prose LLM never decides sharing |

A cut acquires cheap facts for the pictures it can select and their capture context. Captions
and Live Photo checks wait for selected shots and actual candidates. `immich-memories prepare`
reads a whole scope ahead of time when explicitly requested.

Everything is banked per picture and per producer, so changing the tier erases nothing, and a
NAS library can add captions later.

### Which tier you get

Without a supported local GPU runtime or a healthy GPU inference service, automatic selection
stays on NAS. GPU capability selects GPU; a configured LLM alongside it selects Full.
An LLM alone still supplies titles and other text features, and the app explains the missing GPU
capability. It does not start captioning through that LLM.

`immich-memories preflight` checks the producers the resolved tier needs. Legacy preparation-tier
overrides no longer choose a different set. Explicit product tiers remain available for controlled
comparisons; they do not install missing models or start services.
