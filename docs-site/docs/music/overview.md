---
sidebar_position: 1
title: AI Music Overview
---

# AI Music Overview

The music pipeline has three stages:

1. **Mood detection**: A vision LLM looks at keyframes from your video and outputs a structured mood analysis (happy, calm, energetic, etc. plus genre and tempo suggestions).
2. **Music generation**: The multi-provider pipeline takes that mood and sends it to a music generation API. It tries backends in priority order, ACE-Step first, falls back to MusicGen if unavailable or fails.
3. **Stem separation**: Demucs (via MusicGen's API) splits the generated track into vocal and accompaniment stems. This powers intelligent audio ducking: the music volume drops automatically when your video clips have speech or laughter.

## Backend Priority

Both backends are independently enable/disable-able in your config. The pipeline tries them in order:

| Priority | Backend | What it does |
|----------|---------|-------------|
| 1 | ACE-Step | Music generation (higher quality, explicit musical params) |
| 2 | MusicGen | Music generation fallback + Demucs stem separation |

Even when ACE-Step handles all the generation, MusicGen's Demucs endpoint is still used for stem splitting. You want both running if you care about audio ducking.

## Quick Config

Enable AI music in your `config.yaml`:

```yaml
audio:
  auto_music: true
  music_source: "ace_step"  # or "musicgen"
```

Then configure the backends: see [ACE-Step](./ace-step.md), [MusicGen](./musicgen.md), or [Multi-Provider Setup](./multi-provider.md) for details.
