---
sidebar_position: 3
title: MusicGen
---

# MusicGen

Meta's MusicGen handles text-to-music generation and Demucs stem separation via a remote API server. If you're running everything locally with ACE-Step + local Demucs, you don't need MusicGen at all — see [AI Music Overview](./overview.md) for the fully local setup.

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

## When to Use MusicGen

MusicGen is useful in two scenarios:

1. **Standalone generator**: If you don't want ACE-Step, set `music_source: "musicgen"`. The quality is decent but doesn't support explicit musical parameters like BPM and key.
2. **Remote Demucs**: If you can't install the `demucs` Python package locally (e.g., no GPU, Docker-only setup), MusicGen's `/separate` endpoint provides Demucs stem separation over HTTP.

## Demucs Stem Separation

MusicGen's API includes a Demucs endpoint that splits audio into stems for audio ducking. However, you can now run Demucs locally without MusicGen — just `pip install 'immich-memories[demucs]'` and the pipeline auto-detects it.

The stems are:

- **Vocals**: isolated vocal content (usually empty for instrumental tracks)
- **Drums, Bass, Other**: granular instrumental stems (4-stem mode)

The assembler uses these to duck the music volume when your video clips contain speech or laughter.
