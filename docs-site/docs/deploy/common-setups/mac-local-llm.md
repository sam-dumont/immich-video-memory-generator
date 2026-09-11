---
sidebar_label: "Mac + Local LLM"
---

# Mac + Local LLM Setup

For Mac users running everything locally: the reader, the caption server, Apple Silicon hardware acceleration, and a native install without Docker.

## Who this is for

You have a Mac with Apple Silicon (M1/M2/M3/M4) and enough unified memory to hold the models: 32 GB is the tested floor. You want the whole editor running on your own machine, no cloud APIs. You're comfortable with the terminal.

## Architecture

```
┌───────────────────────────────────────────────────┐
│ Mac (Apple Silicon)                               │
│                                                   │
│  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  oMLX        │  │   Immich Memories         │  │
│  │  reader model│←─│   (native Python)         │  │
│  │  port 8000   │  │   VideoToolbox encoding   │  │
│  │              │  │   Taichi titles on Metal  │  │
│  └──────────────┘  └──────────────────────────┘  │
│                             │                     │
│                    ┌────────┴─────────┐           │
│                    │  Immich server   │           │
│                    │  (local or remote)│           │
│                    └──────────────────┘           │
└───────────────────────────────────────────────────┘
```

![Mac setup diagram](/img/diagrams/setup-mac.png)

## Install

```bash
# Install Immich Memories with the Mac extras (Taichi GPU titles, the editorial stack, ...)
uv tool install "immich-memories[all-mac]"

# Start the UI
immich-memories ui
```

The bare `immich-memories` package works too, but title screens are then PIL-rendered and the
context heads and detectors have no runtime: `all-mac` is what installs Taichi and the editorial
stack described below. Note it does not include the `auth` extra; add that separately if you want
OIDC login.

Open [http://localhost:8080](http://localhost:8080).

Also complete [editorial annotation setup](../configuration/editorial-preparation.md): the
public context encoder, detector weights and compact-caption service are separate from the
general model connection below. New runs use story-first selection and the FAMILY audience.

## Set up the reader

The reader groups the period's days into stories, weighs them and picks the pictures, and it is
what *looks* at some of them: the candidates whose facts the edit demands, a few dozen per
memory, reach this endpoint as 800 px tiles. So the seat needs vision and at least a 32k context,
and a text-only model cannot take it.

The graded configuration, the one whose cuts have been approved, is
**`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit`** served by
[oMLX](https://github.com/jundot/omlx). Anything else that speaks the OpenAI
`/v1/chat/completions` contract, accepts images and honours `response_format: json_schema` will
work; it just has not been graded. The older Qwen3.6 pair was exercised against the retired
per-clip scorer, not this route.

oMLX is a menu-bar app that serves MLX models over an OpenAI-compatible API. macOS 15+, Python
3.11-3.13:

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install jundot/omlx/omlx
omlx start        # background service on port 8000
```

Pull a model from the admin dashboard at [http://localhost:8000/admin/chat](http://localhost:8000/admin/chat),
or drop it into the model directory yourself. The weights are on Hugging Face:

| Seat | Repo | Resident |
|------|------|----------|
| Reader | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` | ~17 GB |
| Captions | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` (revision `fa57db46`) | 1–2 GB |

The weights stay resident while the servers are up: read them as the floor for how much unified
memory the models alone take. The caption server is a second service on its own port (8092 by
default) and has to advertise the alias `smolvlm2-500m-base-public`; the
[self-hosting guide](../self-hosting.md) stands both of them up.

Then point Immich Memories at it in `~/.immich-memories/config.yaml`:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://localhost:8000/v1
    model: mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
```

`model` has to match what the server reports at `GET /v1/models`, not the name you typed anywhere else.

Or set via environment variables:

```bash
export IMMICH_MEMORIES_LLM__BASE_URL=http://localhost:8000/v1
export IMMICH_MEMORIES_LLM__MODEL=mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
```

:::note mlx-vlm
[mlx-vlm](https://github.com/Blaizzy/mlx-vlm) is the other common way to serve a vision model on a
Mac and works the same way from this side of the wire. Its README lists Qwen support through 3.5,
so check it covers whatever you load before you count on it.
:::

## What works

- **A local editor**: the model reads the period's pictures and edits the memory on your machine; nothing leaves it.
- **VideoToolbox encoding**: H.264/H.265 encoding on the chip's media engine instead of the CPU cores.
- **Taichi GPU title renderer**: particle effects and gradient backgrounds rendered on Apple GPU.
- **AI music generation**: ACE-Step runs in-process on Apple Silicon via MLX, no server involved. A 60 s track takes ~17 s with `use_lm: false`, or ~45 s with thinking mode on. What it costs is memory, not time: see below.
- **All memory types and features**: everything works natively on Mac.

## Local music generation

ACE-Step's weights have to stay resident for the model to run at all, so memory is the thing that decides whether a profile works on your machine:

| Profile | Weights that must stay resident |
|---------|----------------------------------|
| XL (4B) + 4B planner | ~29 GB |
| XL (4B), `use_lm: false` | ~21 GB |
| 2B + 1.7B planner | ~11 GB |
| 2B, `use_lm: false` | ~7 GB |

A 16 GB Mac runs the 2B profiles. The numbers above are the check: 21 GB free for XL without the
planner, 29 GB with it. And that is free memory, not installed. If the profile does not fit,
`lib` mode says so before loading anything and the run falls back to a bundled track rather than
being killed mid-render.

The config, the pinned install commands and the full memory notes are in [Fully Local Setup](../../create/pipeline/audio-and-music.md#fully-local-setup-no-servers).

## What doesn't work locally

- **MusicGen**: this backend only talks to an API server, so it needs an NVIDIA host or a hosted endpoint. You do not need it if ACE-Step is running: set `musicgen.enabled: false` and local Demucs handles the stem separation that ducking uses.

## Performance expectations

On an M2 Pro (12-core, 32 GB):

| Clips | Resolution | LLM analysis | Total time |
|-------|-----------|-------------|-----------|
| 15 | 1080p | ~3 min | ~5 min |
| 30 | 1080p | ~5 min | ~8 min |
| 30 | 4K | ~5 min | ~14 min |
| 50 | 1080p | ~8 min | ~12 min |

Those numbers are from an earlier 7B vision model (2 frames per clip at ~3 seconds per frame) and
have not been re-measured against the 30B reader: read them as a floor, not a forecast. What
has not changed is the shape: the model passes are the slowest phase, and they are cached. A
second cut over the same period skips them entirely.

Memory is the constraint, not time. Immich Memories itself wants 2-4 GB; the models want their
weights resident for as long as their servers are up: ~17 GB for the reader, 1-2 GB for the
captions.

That is what makes local music generation tighter here than on a machine doing nothing else: a
reader holding 17 GB is exactly the situation where an ACE-Step XL profile stops fitting.
Stopping the model servers before a music-heavy run buys all of it back.

## Tips

- **Start the required model services before generating.** Missing annotation or story providers
  stop an uncached editorial run with an incomplete result. Matching cached facts are reused.
- **The graded reader is the 4-bit one.** A higher-precision build of the same model will run if the memory is there; it is not what the approved sheets came from.
- **Smaller vision models will run** on tighter machines. None of them has been graded on this route: treat the output as your own experiment rather than a supported configuration.
- **Ollama speaks the same contract**, so it works as a transport. Nothing on this route has been run on it, and whatever you serve there still has to accept images.
- **Preparation covers the whole source period.** There is no depth knob and no shortlist: every
  eligible picture is read once, because one the editor never saw is one it cannot weigh. Exact
  producer/input cache hits are reused.
