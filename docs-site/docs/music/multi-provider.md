---
sidebar_position: 4
title: Multi-Provider Setup
---

# Multi-Provider Setup

The ideal setup runs both backends on separate GPUs. ACE-Step handles music generation (better quality), MusicGen handles Demucs stem separation (ACE-Step doesn't provide it).

## Config

```yaml
audio:
  auto_music: true
  music_source: "ace_step"

ace_step:
  enabled: true
  mode: "api"
  api_url: "http://gpu-server-1:8000"   # ACE-Step on GPU 1
  model_variant: "turbo"
  lm_model_size: "0.6B"

musicgen:
  enabled: true
  base_url: "http://gpu-server-2:8000"   # MusicGen on GPU 2
  num_versions: 3
```

## How Fallback Works

The pipeline tries backends in order:

1. Check if ACE-Step is available (health check to its API)
2. If yes, generate with ACE-Step
3. If ACE-Step fails or is unreachable, fall back to MusicGen for generation
4. Regardless of which backend generated, send the audio to MusicGen's Demucs for stem separation

This happens per-version. If ACE-Step generates version 1 successfully but crashes on version 2, MusicGen picks up version 2 automatically. No manual intervention needed.

## Single GPU Setup

You can also run both on the same machine if you only have one GPU. Just point them at the same host with different ports:

```yaml
ace_step:
  enabled: true
  api_url: "http://localhost:8000"

musicgen:
  enabled: true
  base_url: "http://localhost:8001"
```

Keep in mind that running both simultaneously on one GPU means they'll compete for VRAM. With an 8GB card, you'll probably want to run them sequentially rather than in parallel.
