---
sidebar_position: 2
title: uv / pip
---

# Install with uv or pip

Requires Python 3.11 or newer and FFmpeg on your `PATH`. On macOS, `brew install ffmpeg`; on
Debian or Ubuntu, `sudo apt install ffmpeg`. Confirm both `ffmpeg` and `ffprobe` run before
starting a cut.

## uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv tool install "immich-memories[editorial]"
immich-memories --help
```

On Apple Silicon, use `immich-memories[all-mac]` for the Apple bindings, preparation and bundled
music. If the command is missing after installation, run `uv tool update-shell` and open a new
terminal. The [uv tools guide](https://docs.astral.sh/uv/guides/tools/) explains tool environments.

To try the CLI help without a permanent tool install:

```bash
uvx immich-memories --help
```

`uvx` downloads and caches an isolated environment. It does install dependencies, even though it
does not add a permanent command to your shell.

## pip

Use a virtual environment. These commands are for macOS and Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "immich-memories[editorial]"
immich-memories --help
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. Quote package extras so
zsh does not interpret the brackets as a filename pattern.

## From source

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync --extra editorial
uv run immich-memories ui
```

A checkout installs into `.venv`; use `uv run` for its commands. For pip, activate a virtual
environment and run `python -m pip install -e ".[editorial]"` instead.

## Extras

| Extra | Adds |
|---|---|
| `editorial` | ONNX Runtime and Hugging Face Hub for local heads and detectors |
| `editorial-cuda` | GPU ONNX Runtime on Linux, replacing `editorial`; the encoder can use CUDA, detectors remain CPU |
| `mac` | Apple framework bindings; does not include the preparation runtimes |
| `music` | Bundled royalty-free tracks |
| `audio` | Metadata support for a local music library |
| `demucs` | Local stem separation for ducking |
| `auth` | OIDC login through Authlib |
| `all` | Editorial, music, audio, Demucs and auth |
| `all-mac` | Editorial, music, audio, Demucs and Mac bindings; add `auth` for OIDC |

Never combine `editorial` and `editorial-cuda`, including through `all`: both distributions own
`onnxruntime`. Title kernels need no extra; [platform support](../hardware/cpu-only.md#title-kernels)
determines whether Quadrants or PIL is used. ACE-Step has a separate installation procedure in
[Audio and music](../../create/pipeline/audio-and-music.md).

## Before the first cut

```bash
immich-memories config
immich-memories models fetch
immich-memories preflight
```

The default preparation tier, `full`, needs the model files and a separate caption endpoint.
Use `no_captions` without that server, or `metadata_only` to skip ONNX and captions entirely.
The latter can run with the base package. Leave `llm.model` blank for the rules reader.
[Editorial annotation setup](../configuration/editorial-preparation.md) covers these choices.

Preflight checks the encoder and sensitive-content export digests, the caption alias when needed,
and hardware. It does not check every dependency or the document-classifier snapshot; missing
preparation inputs can still stop the first cut.

## Optional system dependencies

ExifTool is a fallback for HDR headroom in unusual Apple HEIC files. The primary parser is Python.
Install it with `brew install exiftool` on macOS or `sudo apt install libimage-exiftool-perl` on
Debian/Ubuntu if the fallback is needed.
