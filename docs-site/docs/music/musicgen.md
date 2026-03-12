---
sidebar_position: 3
title: MusicGen
---

# MusicGen

Meta's MusicGen handles two jobs: text-to-music generation and Demucs stem separation. Even when ACE-Step is your primary generator, MusicGen's Demucs endpoint is what splits tracks into stems for audio ducking.

## Deploy

```bash
docker run -d \
  --gpus all \
  -p 8000:8000 \
  ghcr.io/sam-dumont/musicgen-api:latest
```

That gives you both the generation and Demucs endpoints on port 8000.

## Config

```yaml
musicgen:
  enabled: true
  base_url: "http://localhost:8000"
  api_key: ""                    # optional, for authenticated setups
  timeout_seconds: 10800         # 3 hours max per job (generation can be slow)
  num_versions: 3                # variations to generate
  hemisphere: "north"            # seasonal prompt hints
```

## When to Use MusicGen Alone

MusicGen works fine as a standalone generator if you don't want to set up ACE-Step. Set `music_source: "musicgen"` in your audio config and you're done. The quality is decent: it just doesn't support explicit musical parameters like BPM and key the way ACE-Step does.

## Demucs Stem Separation

This is the real reason to keep MusicGen running even if ACE-Step handles generation. Demucs splits the generated track into:

- **Accompaniment stem**: the instrumental background
- **Vocal stem**: isolated vocals (usually empty for instrumental tracks, but the separation still helps)

The assembler uses these stems to duck the music volume when your video clips contain speech or laughter. Without stems, ducking still works but is less precise.
