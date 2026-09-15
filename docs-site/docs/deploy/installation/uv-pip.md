---
sidebar_position: 2
title: uv / pip
---

# Install with uv or pip

Requires **Python 3.11 or newer** and FFmpeg on your `PATH`:

```bash
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Debian, Ubuntu
```

Install with an extra: a bare install has no ONNX runtime, so the first cut stops at the heads
stage on every tier but `metadata_only`.

## uv

[uv](https://docs.astral.sh/uv/) installs faster than pip, and `uvx` runs the CLI without
installing anything:

```bash
uvx immich-memories --help
```

For something permanent, install it as a tool:

```bash
uv tool install "immich-memories[all]"      # or [all-mac] on Apple Silicon
```

Or work from a clone:

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync --extra editorial      # or --extra all-mac on Apple Silicon
uv run immich-memories ui
```

`uv sync` installs into the clone's `.venv` and puts nothing on your `PATH`: inside the clone it is
always `uv run immich-memories ...`.

To get uv itself: `brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## pip

Use a virtual environment: don't install into your system Python.

```bash
pip install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

Quote the spec: zsh treats `[...]` as a glob and fails with `no matches found` otherwise. From a
checkout, `pip install -e .`.

## Extras

| Extra | What it adds |
|---|---|
| `editorial` | ONNX Runtime and Hugging Face Hub, for the context heads and the two detectors |
| `editorial-cuda` | the same seats on a CUDA host. **Replaces** `editorial`, never joins it |
| `mac` | pyobjc bindings (Quartz, Metal, Vision) for hardware probing. Not enough to cut with alone |
| `music` | the bundled royalty-free track library |
| `audio` | local music library metadata (mutagen) for `immich-memories music search` |
| `auth` | OIDC / SSO login (authlib) |
| `demucs` | local Demucs stem separation for music ducking (Torch, ~80 MB model) |
| `all` | everything, cross-platform |
| `all-mac` | everything, on macOS |

`uv sync --extra <name>` inside a clone, `"immich-memories[<name>]"` everywhere else. Never install
`editorial` and `editorial-cuda` together: `onnxruntime` and `onnxruntime-gpu` own the same import
name and the one that answers is whichever pip wrote last. `all` and `all-mac` carry the CPU one.

Two things need no extra: GPU title rendering, whose kernel library is a base dependency wherever
it publishes a wheel ([Title kernels](../hardware.md#title-kernels)), and AI music generation,
which talks to a server ([Audio and music](../../create/titles-and-music.md#music)). The pinned
encoder, detector weights and caption endpoint the `editorial` runtime needs are on
[editorial annotation setup](../configuration/editorial-preparation.md).

exiftool is worth having on an Apple HEIC library: it is the fallback when the pure Python HDR
headroom parser trips on an unusual file. `brew install exiftool`, or
`apt install libimage-exiftool-perl` on Debian.

## Before the first cut

```bash
immich-memories --help
immich-memories models fetch   # skip on tier: metadata_only
immich-memories preflight
```

`models fetch` writes the pinned encoder, the pinned sensitive-content export and the document
classifier's snapshot, about 500 MB, under `~/.immich-memories/models` and the Hugging Face cache.
`preflight` then says what this install has: a row per digest-pinned ONNX export and one for GPU
title rendering. It checks neither the Hugging Face snapshot nor the other extras; the first cut
does, and stops with a count per missing producer.
