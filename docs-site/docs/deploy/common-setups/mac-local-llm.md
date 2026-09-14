---
sidebar_label: "Mac + Local LLM"
---

# Mac + Local LLM Setup

For Mac users running everything locally: the reader, the caption server, Apple Silicon hardware acceleration, and a native install without Docker.

## Who this is for

You have a Mac with Apple Silicon (M1/M2/M3/M4) and enough unified memory to hold the models: 32 GB is the tested floor. You want the whole editor running on your own machine, no cloud APIs. You're comfortable with the terminal.

## Install

```bash
# Install Immich Memories with the Mac extras (Apple Vision, the editorial stack, ...)
uv tool install "immich-memories[all-mac]"

# Start the UI
immich-memories ui
```

The base package already includes title kernels on supported Apple Silicon Python versions.
`all-mac` adds the editorial preparation dependencies and Apple framework bindings. Note it does not include the `auth` extra; add that separately if you want
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
[oMLX](https://github.com/jundot/omlx). Another server must accept images and honour the same structured-response contract. Its output
quality needs separate testing.

oMLX serves MLX models over an OpenAI-compatible API. Follow its current system requirements;
the Homebrew setup is:

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

Those weights must fit while the models are loaded, with room for context and runtime
overhead. Servers may unload idle models; a running server does not imply resident weights. The caption server is a second service on its own port (8092 by
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

## What works

- **A local editor**: the model reads the period's pictures on your machine. Other optional outbound calls are
  listed in [Network & Privacy](../configuration/network-and-privacy.md).
- **VideoToolbox encoding**: H.264/H.265 encoding on the chip's media engine instead of the CPU cores.
- **GPU title renderer**: particle effects and gradient backgrounds rendered on the Apple GPU.
- **AI music generation**: ACE-Step runs in-process on Apple Silicon via MLX, no server involved. A 60 s track takes ~17 s with `use_lm: false`, or ~45 s with thinking mode on. What it costs is memory, not time: see below.

## Local music generation

ACE-Step's weights have to stay resident for the model to run at all, so memory is the thing that decides whether a profile works on your machine:

| Profile | Weights that must stay resident |
|---------|----------------------------------|
| XL (4B) + 4B planner | ~29 GB |
| XL (4B), `use_lm: false` | ~21 GB |
| 2B + 1.7B planner | ~11 GB |
| 2B, `use_lm: false` | ~7 GB |

A 16 GB Mac runs `2B, use_lm: false` and, on a quiet machine, the 11 GB 2B-plus-planner profile.
The numbers above are the check, and they are free memory, not installed: 21 GB free for XL
without the planner, 29 GB with it. Subtract the 2 to 4 GB the app itself is holding. If the profile does not fit,
`lib` mode says so before loading anything and the run falls back to a bundled track rather than
being killed mid-render.

The config, the pinned install commands and the full memory notes are in [Fully Local Setup](../../create/pipeline/audio-and-music.md#running-it-in-process-on-apple-silicon).

## What doesn't work locally

- **MusicGen**: this backend only talks to an API server, so it needs a reachable MusicGen-compatible service. You do not need it if ACE-Step is running: set `musicgen.enabled: false` and local Demucs handles the stem separation that ducking uses.

## Performance and memory

[Running modes](../running-modes.md) records cold and warm selection and workstation render
measurements. Matching facts are reused; a second cut still has reading and rendering work.

Model memory adds up: the tested reader uses about 17 GB for weights, captions need additional
room, and ACE-Step competes for the same unified memory. Check free memory before choosing a
music profile. The app's peak also depends on source resolution and title settings.

## Tips

- **Start the required model services before generating.** Missing annotation or story providers
  stop an uncached editorial run with an incomplete result. Matching cached facts are reused.
- **The graded reader is the 4-bit one.** A higher-precision build of the same model will run if the memory is there; it is not what the approved sheets came from.
- **Other vision models need testing.** Fitting in memory is only one requirement; check their
  image input, structured responses and output quality.
- **Other model servers need testing.** They must accept images and honour the structured-response contract.
- **Preparation covers the whole source period.** There is no depth knob and no shortlist: every
  eligible picture is read once, because one the editor never saw is one it cannot weigh. Exact
  producer/input cache hits are reused.
