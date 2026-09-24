---
sidebar_position: 2
title: uv / pip
---

# uv or pip

Reader: power user.

For a Mac, a Linux box without Docker, or a checkout you want to hack on. Docker Compose is the
[recommended install](./docker.md); this one gives the same app with your Python.

You need **Python 3.11 or newer** and FFmpeg on your `PATH`:

```bash
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Debian, Ubuntu
```

## Install

Always with an extra. A bare install has no ONNX Runtime, and the first cut refuses to start on
every tier but `metadata_only`.

```bash
uv tool install "immich-memories[all]"       # or [all-mac] on Apple Silicon
immich-memories models fetch                 # about 130 MB, once
immich-memories preflight
immich-memories ui                           # http://localhost:8080
```

Or with pip, in a virtual environment (not your system Python):

```bash
pip install "immich-memories[editorial]"     # or [all-mac] on Apple Silicon
```

Quote the spec: zsh reads `[...]` as a glob and fails with `no matches found` otherwise.

To try it without installing anything:
`uvx --from "immich-memories[editorial]" immich-memories ui`. To get uv itself: `brew install uv`,
or `curl -LsSf https://astral.sh/uv/install.sh | sh`.

`models fetch` writes the encoder to `~/.immich-memories/models/triage/`, the sensitive-content
detector to `~/.immich-memories/models/detectors/`, and the document classifier into the Hugging
Face cache (`~/.cache/huggingface`, or `advanced.editorial.preparation.detector_cache_dir`). Each is
checked against a SHA-256. It needs the `editorial` extra (every `all*` extra has it).

A cut checks the two ONNX files and the output folder (`~/Videos/Memories` by default) before it
asks Immich for anything, and stops with `Run immich-memories models fetch` if a model is missing.
Then set your Immich URL and key, and your home base, in `~/.immich-memories/config.yaml`:
[Configuration file](./config-file.md), or [Environment variables](./environment-variables.md).

## From a checkout

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync --extra editorial      # or --extra all-mac on Apple Silicon
uv run immich-memories ui
```

`uv sync` installs into the clone's `.venv` and puts nothing on your `PATH`: inside the clone it is
always `uv run immich-memories ...`. `pip install -e .` works too.

## Extras

| Extra | What it adds |
|---|---|
| `editorial` | ONNX Runtime and Hugging Face Hub, for the context heads and the two detectors. The one you need |
| `editorial-cuda` | The same on a CUDA host. **Replaces** `editorial`, never joins it |
| `mac` | pyobjc bindings (Quartz, Metal, Vision) for hardware probing. Not enough to cut with alone |
| `music` | The bundled royalty-free track library |
| `audio` | Local music metadata (mutagen) for `immich-memories music search` |
| `auth` | OIDC login (authlib) |
| `demucs` | Local Demucs stem separation for music ducking (Torch, about 80 MB of model) |
| `all` | All of the above except `mac` and `editorial-cuda` |
| `all-mac` | `editorial`, `mac`, `music`, `audio` and `demucs`. No `auth`: add `[all-mac,auth]` for OIDC |

Never install `editorial` and `editorial-cuda` together: `onnxruntime` and `onnxruntime-gpu` own
the same import name, and the one that answers is whichever pip wrote last.

GPU title rendering needs no extra: its kernel library is a base dependency wherever it publishes a
wheel ([Title kernels](./hardware.md#title-kernels)). Local ACE-Step music on a Mac is a checkout
job (`make install-acestep`, then `make check-local-audio`): [Generated music](../better/music.md).

exiftool is worth having on an Apple HEIC library: it is the fallback when the Python HDR headroom
parser trips on an unusual file. `brew install exiftool`, or `apt install libimage-exiftool-perl`.

## Updating

```bash
uv tool upgrade immich-memories
immich-memories models fetch       # a no-op unless a release moved a pin
```
