---
sidebar_label: "Mac + Local LLM"
---

# Mac + Local LLM

One Apple Silicon Mac running the whole editor: the app native, the reader and the caption server
as local services, VideoToolbox for the encode, nothing leaving the machine. This is the
configuration the project is graded on. 32 GB of unified memory is the tested floor, because the
reader alone holds about 17 GB of weights for as long as its server is up.

## Architecture

![Mac setup diagram](/img/diagrams/setup-mac.png)

## Install

```bash
# Install Immich Memories with the Mac extras (Apple Vision, the editorial stack, ...)
uv tool install "immich-memories[all-mac]"

# Start the UI
immich-memories ui
```

Open [http://localhost:8080](http://localhost:8080).

The bare `immich-memories` package works too, but title screens are then PIL-rendered and the
context heads and detectors have no runtime: `all-mac` is what installs the editorial
stack described below. Note it does not include the `auth` extra; add that separately if you want
OIDC login.

Then work through [editorial annotation setup](../configuration/editorial-preparation.md): the
public context encoder, the detector weights and the caption service are separate from the reader
connection below.

## Set up the reader

The reader needs vision and at least a 32k context. It groups the period's days into stories,
weighs them and picks the pictures, and the candidates whose facts the edit demands reach it as
800 px tiles, a few dozen per memory. A text-only model cannot take this seat.

The graded configuration, the one whose cuts have been approved, is
**`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit`** served by
[oMLX](https://github.com/jundot/omlx), a menu-bar app that serves MLX models over an
OpenAI-compatible API. macOS 15+, Python 3.11-3.13:

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

Both stay resident while their servers are up: read the table as the floor for what the models
alone take out of your unified memory.

Anything else that speaks the OpenAI `/v1/chat/completions` contract, accepts images and honours
`response_format: json_schema` will work; it just has not been graded. Ten readers were pointed at
one real month and three of them stopped: [Readers](../readers.md).

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

## Set up the captions

On the `full` tier, a second service on its own port (8092 by default) writes one sentence under
every picture. oMLX cannot load SmolVLM2 at all, so this is a separate process from the reader you
just started. On a Mac it is mlxcel and two commands, and the app checks one thing before it sends
a picture: that `GET /models` advertises `smolvlm2-500m-base-public`. The recipe, the alias and
what happens when either is wrong are on
[Caption server](../installation/caption-server.md#apple-silicon-with-mlxcel).

## What works

- **A local editor**: the model reads the period's pictures and edits the memory on your machine; nothing leaves it.
- **VideoToolbox encoding**: H.264/H.265 on the chip's media engine instead of the CPU cores.
- **GPU title renderer**: particle effects and gradient backgrounds on the Apple GPU.
- **ACE-Step music in-process**: no server involved. A 60 s track takes ~17 s with `use_lm: false`, or ~45 s with thinking mode on. What it costs is memory, not time.

MusicGen is the exception: that backend only talks to an API server, so it needs an NVIDIA host or
a hosted endpoint. You do not need it if ACE-Step is running: set `musicgen.enabled: false` and
local Demucs handles the stem separation that ducking uses.

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

## What to expect

A Mac is one of the three hosts the setup matrix measured end to end, cold and warm, on a real
month: [Running modes](../running-modes.md).

The shape does not change: the model passes are the slow phase, they scale with how many candidate
pictures the period holds rather than with how long the video is, and they are banked. A second
cut over the same period skips them.

Memory is the constraint here, not time. The app wants 2-4 GB; the reader holds ~17 GB and the
captioner 1-2 GB for as long as their servers are up. That is what makes local music generation
tighter on this machine than on one doing nothing else: a reader holding 17 GB is exactly the
situation where an ACE-Step XL profile stops fitting. Stopping the model servers before a
music-heavy run buys all of it back.

## Tips

- **Start the model services before generating.** A missing annotation or story provider stops an uncached editorial run with an incomplete result. Facts already banked are reused.
- **The graded reader is the 4-bit one.** A higher-precision build of the same model will run if the memory is there; it is not what the approved sheets came from.
- **Smaller vision models will run** on tighter machines. None of them has been graded on this route: treat the output as your own experiment rather than a supported configuration.
- **Ollama speaks the same contract**, so it works as a transport. Nothing on this route has been run on it, and whatever you serve there still has to accept images.
- **Preparation covers the whole source period.** There is no depth knob and no shortlist: every eligible picture is read once, because one the editor never saw is one it cannot weigh.
