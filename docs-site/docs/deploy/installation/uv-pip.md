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

## uv (Recommended)

[uv](https://docs.astral.sh/uv/) resolves and installs faster than pip, and `uvx` runs the CLI without installing anything.

### One-Liner (No Install Required)

Run directly without installing anything:

```bash
uvx immich-memories --help
```

`uvx` creates an isolated environment, runs the command, done. Great for trying things out.

### Clone and Install

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync --extra editorial      # or --extra all-mac on Apple Silicon
uv run immich-memories ui
```

A bare `uv sync` gives you the app and the render and not the ONNX runtime the six context heads
and the two detectors need, so the first cut stops at the heads stage on every tier but
`metadata_only`. Take the extra.

`uv sync` installs into the clone's `.venv` and puts nothing on your `PATH`: inside the clone it is
always `uv run immich-memories ...`. For a plain `immich-memories` command, install it as a tool
instead: `uv tool install "immich-memories[all]"` (or `[all-mac]` on macOS).

### Platform Extras

Install optional features depending on your setup:

```bash
# macOS: pyobjc bindings (Quartz, Metal, Vision) used for hardware probing.
# Not enough to cut with on its own: see all-mac below.
uv sync --extra mac

# Bundled royalty-free music tracks
uv sync --extra music

# Local music library metadata (mutagen) for `immich-memories music search`
uv sync --extra audio

# Public context heads and detectors for editorial annotation preparation
uv sync --extra editorial

# OIDC / SSO login (authlib)
uv sync --extra auth

# Local Demucs stem separation for music ducking (Torch, ~80 MB model)
uv sync --extra demucs

# Everything (cross-platform)
uv sync --extra all

# Everything on macOS
uv sync --extra all-mac
```

GPU-accelerated title rendering (Metal, CUDA, Vulkan) needs no extra: the kernel library is a base
dependency wherever it publishes a wheel. See
[Title kernels](../hardware/cpu-only.md#title-kernels) for the platforms that have one.

The `music` extra is the bundled royalty-free track library (in both `all` and `all-mac`). AI music
generation (ACE-Step, MusicGen) is a different thing and is not a pip extra: it talks to a server
or an in-process ACE-Step install; see
[Audio and music](../../create/pipeline/audio-and-music.md).

### Install uv

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

### From PyPI

```bash
pip install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

A bare `pip install immich-memories` gives you the app and the render, but not the inference
dependencies the six context heads and the two ONNX detectors need, so the first cut stops at the heads
stage. Take the extra.

### From Source

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
pip install -e .
```

### Extras

Quote the package spec: zsh (the macOS default shell) treats `[...]` as a glob and fails with
`no matches found` otherwise.

```bash
# macOS Apple Vision framework
pip install "immich-memories[mac]"

# Bundled royalty-free music tracks
pip install "immich-memories[music]"

# Local music library metadata (mutagen)
pip install "immich-memories[audio]"

# Public context heads and detectors for editorial annotation preparation
pip install "immich-memories[editorial]"

# OIDC / SSO login
pip install "immich-memories[auth]"

# Local Demucs stem separation
pip install "immich-memories[demucs]"

# Everything (cross-platform)
pip install "immich-memories[all]"

# Everything on macOS
pip install "immich-memories[all-mac]"
```

`editorial-cuda` **replaces** `editorial`; never install both. `onnxruntime` and `onnxruntime-gpu`
own the same import name, and the one that answers is whichever pip wrote last. `all` and `all-mac`
carry the CPU variant.

The `editorial` extra supplies the runtimes for preparing missing public context and detector
facts. Its pinned encoder, detector weights and compact-caption endpoint require separate
[editorial annotation setup](../configuration/editorial-preparation.md). Complete cached facts
skip these providers; missing required facts stop selection with an explicit setup error.

### Check what this install actually has

```bash
immich-memories preflight
```

GPU title rendering gets a row saying what it costs where the kernel library has no wheel, and
both digest-pinned ONNX exports get a row each. What preflight does not check is the document
classifier's Hugging Face snapshot, or whether the other extras are installed: the first cut does
that, and stops with a count per missing producer.

## Optional System Dependencies

These are **not required** but improve specific features:

| Tool | What it does | Install |
|------|-------------|---------|
| [exiftool](https://exiftool.org/) | Fallback for HDR headroom extraction from Apple HEIC photos | `brew install exiftool` (macOS) / `apt install libimage-exiftool-perl` (Debian) |

The primary HDR headroom parser is pure Python: exiftool is only called if the built-in parser fails on an unusual HEIC file.

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
