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

Take an extra with the install. A bare one gives you the app and the render and not the ONNX
runtime the six context heads and the two detectors need, so the first cut stops at the heads stage
on every tier but `metadata_only`.

## uv

[uv](https://docs.astral.sh/uv/) resolves and installs faster than pip, and `uvx` runs the CLI
without installing anything:

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

If you don't have uv yet:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via Homebrew
brew install uv
```

## pip

Works fine, just slower than uv. Use a virtual environment: don't install into your system Python.

```bash
pip install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

Quote the spec: zsh (the macOS default shell) treats `[...]` as a glob and fails with
`no matches found` otherwise.

From a checkout:

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
pip install -e .
```

## Extras

| Extra | What it adds |
|---|---|
| `editorial` | ONNX Runtime and Hugging Face Hub, for the context heads and the two detectors |
| `editorial-cuda` | the same seats on a CUDA host. **Replaces** `editorial`, never joins it |
| `mac` | pyobjc bindings (Quartz, Metal, Vision) for hardware probing. Not enough to cut with on its own |
| `music` | the bundled royalty-free track library |
| `audio` | local music library metadata (mutagen) for `immich-memories music search` |
| `auth` | OIDC / SSO login (authlib) |
| `demucs` | local Demucs stem separation for music ducking (Torch, ~80 MB model) |
| `all` | everything, cross-platform |
| `all-mac` | everything, on macOS |

`uv sync --extra <name>` inside a clone, `"immich-memories[<name>]"` everywhere else.

Never install `editorial` and `editorial-cuda` together: `onnxruntime` and `onnxruntime-gpu` own
the same import name, and the one that answers is whichever pip wrote last. `all` and `all-mac`
carry the CPU variant.

The `editorial` extra is the runtime and nothing else. Its pinned encoder, detector weights and
compact-caption endpoint are [editorial annotation setup](../configuration/editorial-preparation.md).

GPU title rendering (Metal, CUDA, Vulkan) needs no extra: the kernel library is a base dependency
wherever it publishes a wheel. See [Title kernels](../hardware/cpu-only.md#title-kernels) for the
platforms that have one. AI music generation (ACE-Step, MusicGen) is not an extra either: it talks
to a server or an in-process ACE-Step install, see
[Audio and music](../../create/pipeline/audio-and-music.md).

exiftool is worth having on an Apple HEIC library. It is the fallback when the built-in pure Python
HDR headroom parser trips on an unusual file: `brew install exiftool` on macOS,
`apt install libimage-exiftool-perl` on Debian.

## Before the first cut

```bash
immich-memories --help
immich-memories models fetch   # skip on tier: metadata_only
immich-memories preflight
```

`models fetch` writes the pinned encoder, the pinned sensitive-content export and the document
classifier's snapshot, about 500 MB in total, under `~/.immich-memories/models` and the Hugging
Face cache. Every tier but `metadata_only` wants them, and the first cut without them stops at the
heads stage naming the file it could not open.

`preflight` says what this install actually has: a row for GPU title rendering naming what it costs
where the kernel library has no wheel, and a row per digest-pinned ONNX export. It does not check
the document classifier's Hugging Face snapshot or whether the other extras are installed. The
first cut does that, and stops with a count per missing producer.
