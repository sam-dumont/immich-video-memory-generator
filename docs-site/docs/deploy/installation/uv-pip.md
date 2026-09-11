---
sidebar_position: 2
title: uv / pip
---

# Install with uv or pip

Requires **Python 3.11 or newer** and FFmpeg on your `PATH`.

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
uv sync
uv run immich-memories ui      # `uv sync` installs into .venv, so the command is not on your PATH
```

To get a plain `immich-memories` command instead, install it as a tool: `uv tool install "immich-memories[all]"`
(or `[all-mac]` on macOS).

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

# GPU-accelerated title rendering (Metal, CUDA, Vulkan)
uv sync --extra gpu

# Everything (cross-platform)
uv sync --extra all

# Everything on macOS
uv sync --extra all-mac
```

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
dependencies the six context heads and the two detectors need, so the first cut stops at the heads
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

# GPU-accelerated rendering
pip install "immich-memories[gpu]"

# Everything (cross-platform)
pip install "immich-memories[all]"

# Everything on macOS
pip install "immich-memories[all-mac]"
```

The `editorial` extra supplies the runtimes for preparing missing public context and detector
facts. Its pinned encoder, detector weights and compact-caption endpoint require separate
[editorial annotation setup](../configuration/editorial-preparation.md). Complete cached facts
skip these providers; missing required facts stop selection with an explicit setup error.

### Check what this install actually has

```bash
immich-memories preflight
```

GPU title rendering gets a row saying what it costs when Taichi is absent. The other extras do
not yet, and preflight does not check the detector snapshots at all: the first cut does that, and
stops with a count per missing producer.

## Optional System Dependencies

These are **not required** but improve specific features:

| Tool | What it does | Install |
|------|-------------|---------|
| [exiftool](https://exiftool.org/) | Fallback for HDR headroom extraction from Apple HEIC photos | `brew install exiftool` (macOS) / `apt install libimage-exiftool-perl` (Debian) |

The primary HDR headroom parser is pure Python: exiftool is only called if the built-in parser fails on an unusual HEIC file.

## Verify

```bash
immich-memories --help
```
