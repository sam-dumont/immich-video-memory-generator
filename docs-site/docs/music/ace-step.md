---
sidebar_position: 2
title: ACE-Step
---

# ACE-Step

ACE-Step 1.5 generates higher-quality instrumental tracks than MusicGen. It supports explicit musical parameters (BPM, key, time signature) passed as structured API fields rather than crammed into a text prompt.

About ~20 seconds per 30-second track on an 8GB GPU.

## Model Variants

| Variant | Steps | Speed (30s track) | Quality |
|---------|-------|-------------------|---------|
| `turbo` | 8 | ~20s on 8GB GPU | Fast but MIDI-sounding, synthetic timbres |
| `base` | 50 | ~2min on 8GB GPU | More realistic instruments, natural dynamics |

**Recommendation**: Use `base` for final exports (better audio quality). Use `turbo` for quick previews during editing.

The recommended fork is [sam-dumont/ace-step-1.5-turbo](https://github.com/sam-dumont/ace-step-1.5-turbo), which ships both variants.

## Language Model Sizes

The `lm_model_size` setting controls how much VRAM the language model uses for "thinking mode":

| Size | VRAM Required | Notes |
|------|--------------|-------|
| `0.6B` | ~8GB | Fits on budget GPUs |
| `1.7B` | ~12GB | Good balance |
| `4B` | ~16GB+ | Best quality, needs serious hardware |

## Deploy

Run the ACE-Step API server with Docker Compose:

```bash
docker compose -f deploy/ace-step/docker-compose.yaml up -d
```

## Config

```yaml
ace_step:
  enabled: true
  mode: "api"                    # "api" for remote server, "lib" for local
  api_url: "http://localhost:8000"
  model_variant: "turbo"         # "turbo" (8 steps) or "base" (50 steps)
  lm_model_size: "0.6B"          # "0.6B", "1.7B", or "4B"
  use_lm: true                   # disable to save memory/time
  bf16: true                     # set false for Pascal/older GPUs
  num_versions: 3                # how many variations to generate
  hemisphere: "north"            # for seasonal prompt hints
```

## How It Works

The pipeline converts your video's detected mood into ACE-Step's structured format:

- **Caption**: Descriptive natural-language prompt (e.g., "Gentle acoustic folk instrumental with fingerpicked guitar, soft brushed percussion..."). Descriptive sentences work better than comma-separated tag lists.
- **Lyrics**: Section-tagged instrumental markers (`[Intro]\n[Instrumental]\n[Verse]\n[Instrumental]`)
- **BPM, key, time signature**: Sent as explicit API fields (not embedded in the caption text)
- **Instrumental flag**: Explicitly set to prevent the model from generating vocals

The pipeline detects each clip's mood and maps it to a genre template (lo-fi, acoustic, cinematic, etc.), so different video moods produce different music styles.
