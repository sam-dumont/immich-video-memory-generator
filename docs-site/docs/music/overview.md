---
sidebar_position: 1
title: AI Music Overview
---

# AI Music Overview

The music pipeline has three stages:

1. **Mood detection**: A vision LLM looks at keyframes from your video and outputs a structured mood analysis (happy, calm, energetic, etc. plus genre and tempo suggestions).
2. **Music generation**: The multi-provider pipeline takes that mood and sends it to a music generation backend. It tries backends in priority order, ACE-Step first, falls back to MusicGen if unavailable or fails.
3. **Stem separation**: Demucs splits the generated track into vocal and accompaniment stems. This powers intelligent audio ducking: the music volume drops automatically when your video clips have speech or laughter.

## Backend Priority

Both generation backends are independently enable/disable-able. Stem separation auto-detects the best available option:

| Priority | Backend | What it does |
|----------|---------|-------------|
| 1 | ACE-Step | Music generation (higher quality, explicit musical params) |
| 2 | MusicGen | Music generation fallback |

| Priority | Stem Separator | How it works |
|----------|---------------|-------------|
| 1 | MusicGen API | Remote Demucs via `/separate` endpoint (if MusicGen enabled) |
| 2 | Local Demucs | In-process, auto-detected when `demucs` package is installed |

If you have `demucs` installed and no MusicGen server, stem separation just works — zero config.

## Fully Local Setup (No Servers)

On Apple Silicon with enough memory (16GB+), you can run the entire pipeline in-process:

```yaml
audio:
  auto_music: true
  music_source: "ace_step"

ace_step:
  enabled: true
  mode: "lib"              # Direct Python import, no API server
  model_variant: "base"    # "turbo" for fast previews, "base" for quality
  lm_model_size: "4B"      # "0.6B", "1.7B", or "4B"

musicgen:
  enabled: false           # Not needed — local Demucs handles stems
```

Install the packages:

```bash
pip install 'immich-memories[local-audio]'
```

Or separately:

```bash
pip install 'immich-memories[demucs]'       # Stem separation only
pip install 'immich-memories[ace-step]'      # Music generation only
```

That's it. No Docker, no API servers. The pipeline auto-detects local Demucs for stem separation.

:::warning Python version requirement
ACE-Step local (`mode: "lib"`) requires **Python ≤ 3.12**. This is because ACE-Step depends on `spacy==3.8.4` which has no Python 3.13 wheels yet. Demucs works on any Python version.

If your project uses Python 3.13, either:
- Create a separate Python 3.12 virtualenv for local audio: `uv venv .venv --python 3.12`
- Use ACE-Step in API mode (`mode: "api"`) which has no Python version restriction
:::

## Model Cache & Disk Usage

Both ACE-Step and Demucs download models on first run. Here's where they go and how big they are:

| Model | Cache Location | Size | When Downloaded |
|-------|---------------|------|----------------|
| ACE-Step turbo | `~/.cache/huggingface/hub/` | ~2 GB | First generation |
| ACE-Step base | `~/.cache/huggingface/hub/` | ~2 GB | First generation |
| ACE-Step LM 0.6B | `~/.cache/huggingface/hub/` | ~1.2 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 1.7B | `~/.cache/huggingface/hub/` | ~3.4 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 4B | `~/.cache/huggingface/hub/` | ~8 GB | First generation (if `use_lm: true`) |
| Demucs htdemucs | `~/.cache/torch/hub/` | ~80 MB | First stem separation |

**Total disk for a full local setup** (base + 4B LM + Demucs): ~10 GB of cached models.

Tip: if you're low on disk, use `turbo` + `0.6B` — that's ~3.3 GB total.

## Memory Usage

| Component | Peak RAM | Notes |
|-----------|---------|-------|
| ACE-Step turbo + 0.6B LM | ~8 GB | Minimum viable config |
| ACE-Step base + 4B LM | ~16 GB | Production quality |
| Demucs htdemucs | ~2-4 GB | For a 3-minute track |

Components run sequentially (generate, then separate), so peak is `max(ACE-Step, Demucs)`, not the sum.

## Quick Config

Enable AI music in your `config.yaml`:

```yaml
audio:
  auto_music: true
  music_source: "ace_step"  # or "musicgen"
```

Then configure the backends: see [ACE-Step](./ace-step.md), [MusicGen](./musicgen.md), or [Multi-Provider Setup](./multi-provider.md) for details.
