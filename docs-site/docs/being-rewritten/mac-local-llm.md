---
sidebar_label: "Mac + Local LLM"
unlisted: true
---

:::note[Being rewritten]

This page is being split into the new docs. Its text moves to [The reader: config and measurements](../better/reader.md).

:::

# Mac + Local LLM

One Apple Silicon Mac running the whole editor: the app native, the reader and the caption server
as local services, VideoToolbox for the encode, nothing leaving the machine. This is the
configuration the project is graded on. 32 GB of unified memory is the tested floor, because the
reader alone holds about 17 GB of weights for as long as its server is up.

![Mac setup diagram](/img/diagrams/setup-mac.png)

## Install

```bash
uv tool install "immich-memories[all-mac]"
immich-memories ui
```

Open [http://localhost:8080](http://localhost:8080).

The bare `immich-memories` package works too, but title screens are then PIL-rendered and the
context heads and detectors have no runtime: `all-mac` is what installs the editorial stack. It
does not include the `auth` extra; add that separately if you want OIDC login.

Then work through [editorial annotation setup](./editorial-preparation.md): the
context encoder, the detector weights and the caption service are separate from the reader below.

## Set up the reader

The reader needs vision and at least a 32k context: the candidates whose facts the edit demands
reach it as 800 px tiles, a few dozen per memory. A text-only model cannot take this seat.

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
or drop it into the model directory yourself. The weights are on Hugging Face, and both seats stay
resident while their servers are up:

| Seat | Repo | Resident |
|------|------|----------|
| Reader | `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` | ~17 GB |
| Captions | `mlx-community/SmolVLM2-500M-Video-Instruct-mlx` (revision `fa57db46`) | 1-2 GB |

Then point Immich Memories at it in `~/.immich-memories/config.yaml`:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://localhost:8000/v1
    model: mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
```

`model` has to match what the server reports at `GET /v1/models`, not the name you typed anywhere
else. The environment form is `IMMICH_MEMORIES_LLM__BASE_URL` and `IMMICH_MEMORIES_LLM__MODEL`.

Anything else that speaks the OpenAI `/v1/chat/completions` contract, accepts images and honours
`response_format: json_schema` will work; it just has not been graded. Ten readers were pointed at
one real month and three of them stopped: [Readers](../better/reader.md).
[mlx-vlm](https://github.com/Blaizzy/mlx-vlm) is the other common way to serve a vision model on a
Mac and works the same way from this side of the wire.

## Set up the captions

On the `full` tier a second service on its own port (8092 by default) writes one sentence under
every picture. oMLX cannot load SmolVLM2 at all, so this is a separate process from the reader you
just started. On a Mac it is mlxcel and two commands, and the app checks that `GET /models`
advertises `smolvlm2-500m-base-public` before it sends a picture. The recipe is on
[Caption server](../better/captions.md#apple-silicon-with-mlxcel).

## Add local music generation

ACE-Step needs a separate installation; `uv tool install "immich-memories[all-mac]"` does not
include it. Follow the [local ACE-Step setup](../make/titles-maps-music.md#install-locally-on-a-mac)
to use the source checkout's Python 3.12 environment. From that checkout:

```bash
make install-acestep
make check-local-audio
uv run immich-memories ui
```

The installer includes Demucs. The check generates a real 15-second track and all four stems
locally. It prints the audio files and fails on errors, so a bundled fallback cannot hide a broken
installation. Configure `advanced.ace_step.enabled: true`, `advanced.ace_step.mode: lib` and
`advanced.musicgen.enabled: false` to use both models on this Mac.

## What this machine does that others do not

VideoToolbox takes the H.264/H.265 encode onto the media engine, the GPU title renderer gets the
particle effects and gradient backgrounds, and ACE-Step generates music locally with no server
involved: a 60 s track takes ~17 s with `use_lm: false`, or ~45 s with thinking mode on.

MusicGen is the exception. That backend only talks to an API server, so it needs an NVIDIA host or
a hosted endpoint. You do not need it if ACE-Step is running: set `musicgen.enabled: false` and
local Demucs handles the stem separation that ducking uses.

## Memory is the constraint, not time

The app wants 2-4 GB, the reader holds ~17 GB and the captioner 1-2 GB for as long as their servers
are up. ACE-Step's weights have to stay resident too: the XL profile needs 21 GB free without its
planner and 29 GB with it, so a reader holding 17 GB is exactly the situation where XL stops
fitting. Stopping the model servers before a music-heavy run buys all of it back. `lib` mode checks
free memory before it loads anything and falls back to a bundled track rather than being killed
mid-render. Every profile and its floor is in
[Audio and music](../make/titles-maps-music.md#music).

Start the model services before generating: a missing annotation or story provider stops an
uncached editorial run with an incomplete result. Facts already banked are reused.

A Mac is one of the three hosts the setup matrix measured end to end, cold and warm, on a real
month: [Running modes](./running-modes.md). The model passes are the slow phase, they scale with
how many candidate pictures the period holds rather than with how long the video is, and they are
banked, so a second cut over the same period skips them.
