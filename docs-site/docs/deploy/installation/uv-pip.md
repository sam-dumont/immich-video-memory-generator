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
uv run immich-memories ui      # `uv sync` installs into .venv — the command is not on your PATH
```

To get a plain `immich-memories` command instead, install it as a tool: `uv tool install "immich-memories[all]"`
(or `[all-mac]` on macOS).

### Platform Extras

Install optional features depending on your setup:

```bash
# macOS: Apple Vision framework for face detection + GPU rendering
uv sync --extra mac

# Bundled royalty-free music tracks
uv sync --extra music

# Local music library metadata (mutagen) for `immich-memories music search`
uv sync --extra audio

# Semantic audio labels (PANNs + Torch: laughter, speech, babies, music)
uv sync --extra audio-ml

# OIDC / SSO login (authlib)
uv sync --extra auth

# Local Demucs stem separation for music ducking (Torch, ~80 MB model)
uv sync --extra demucs

# Speech boundaries (FireRedVAD, ~15 MB, no Torch)
uv sync --extra speech

# Speech transcription (whisper.cpp — what was said in a clip)
uv sync --extra transcribe

# GPU-accelerated title rendering (Metal, CUDA, Vulkan)
uv sync --extra gpu

# Everything (cross-platform)
uv sync --extra all

# Everything on macOS
uv sync --extra all-mac
```

The `music` extra is the bundled royalty-free track library (in both `all` and `all-mac`). AI music
generation (ACE-Step, MusicGen) is a different thing and is not a pip extra — it talks to a server
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
pip install immich-memories
```

### From Source

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
pip install -e .
```

### Extras

Quote the package spec — zsh (the macOS default shell) treats `[...]` as a glob and fails with
`no matches found` otherwise.

```bash
# macOS Apple Vision framework
pip install "immich-memories[mac]"

# Bundled royalty-free music tracks
pip install "immich-memories[music]"

# Local music library metadata (mutagen)
pip install "immich-memories[audio]"

# Semantic audio labels (PANNs + Torch)
pip install "immich-memories[audio-ml]"

# Speech boundaries (FireRedVAD, no Torch)
pip install "immich-memories[speech]"

# Speech transcription (whisper.cpp)
pip install "immich-memories[transcribe]"

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

The `audio-ml` extra is optional because Torch and PANNs are large. Without it, audio-content
analysis uses an energy-only fallback: it still finds loud/quiet structure, but it cannot reliably
label laughter, babies, speech, or music.

The `speech` extra adds onnxruntime and kaldi-native-fbank (~15 MB, no Torch). The FireRedVAD
weights ship inside the package, so nothing is downloaded at runtime. Without the extra, clip
boundaries fall back to PANNs speech tags, which merge a whole noisy clip into one protected
range and leave boundary adjustment nowhere to move. Both `all` and `all-mac` include it.

The `transcribe` extra adds pywhispercpp, which ships prebuilt wheels for macOS arm64 (with
Metal), Linux x86_64 and aarch64, and Windows — nothing compiles at install. Linux wheels are
CPU-only, which is fine for the 30-second windows this sends it. Model weights are **not**
bundled: they are fetched from HuggingFace on first use, about 1.5 GB for the `medium` default.
Set `advanced.transcription.model: base` for a ~148 MB download at a measured cost in accuracy.
Without the extra, no transcripts are produced and nothing else changes. Both `all` and `all-mac`
include it.

### Check what this install actually has

```bash
immich-memories preflight
```

Every optional feature gets a row saying what it costs when its extra is absent — speech
boundaries, transcription, semantic audio labels, GPU title rendering. A missing runtime is a
`WARNING`, never a silent no-op: the pipeline also logs one warning at startup when speech
boundaries are enabled but the FireRedVAD runtime is not installed.

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
