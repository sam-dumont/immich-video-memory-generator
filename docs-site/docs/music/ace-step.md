---
sidebar_position: 2
title: ACE-Step
---

# ACE-Step

ACE-Step 1.5 generates higher-quality instrumental tracks than MusicGen. It supports explicit musical parameters (BPM, key, time signature) passed as structured API fields rather than crammed into a text prompt.

## Modes

ACE-Step runs in two modes:

| Mode | How it works | When to use |
|------|-------------|-------------|
| `lib` | Direct Python import, in-process | Apple Silicon / CUDA desktop, no Docker needed |
| `api` | Remote REST API server | Headless servers, Docker deployments, Python 3.13 |

## Model Variants

| Variant | Steps | Speed (60s track, M5 Max) | Quality |
|---------|-------|--------------------------|---------|
| `turbo` | 8 | ~8s | Fast preview, synthetic timbres |
| `base` | 60 | ~40s | Production quality, natural instruments |

**Recommendation**: Use `base` for final exports. Use `turbo` for quick previews.

## Local Setup (lib mode)

```bash
# ACE-Step is not on PyPI — install directly from GitHub
pip install 'ace-step @ git+https://github.com/ace-step/ACE-Step.git'
pip install 'torchcodec>=0.1'
```

```yaml
ace_step:
  enabled: true
  mode: "lib"
  model_variant: "base"      # or "turbo" for fast previews
  bf16: true
  num_versions: 1
```

First run downloads the model (~3.5GB) to `~/.cache/ace-step/checkpoints/`.

:::warning Python ≤ 3.12 required
Local mode requires Python ≤ 3.12 because ACE-Step depends on `spacy==3.8.4` which has no Python 3.13 wheels. Use `uv venv .venv --python 3.12` if needed. API mode works on any Python version.
:::

## API Setup

Run the ACE-Step API server with Docker Compose:

```bash
docker compose -f deploy/ace-step/docker-compose.yaml up -d
```

```yaml
ace_step:
  enabled: true
  mode: "api"
  api_url: "http://localhost:8000"
  model_variant: "turbo"
  num_versions: 3
  hemisphere: "north"
```

## Memory Usage

| Config | Peak RAM | Notes |
|--------|---------|-------|
| `turbo` + bf16 | ~8 GB | Budget GPU / 16GB Mac |
| `base` + bf16 | ~16-20 GB | M-series Mac with 32GB+ |

## Model Cache

| Component | Location | Size |
|-----------|----------|------|
| ACE-Step v1 3.5B | `~/.cache/ace-step/checkpoints/` | ~3.5 GB |

The model downloads automatically from HuggingFace on first generation.

## How It Works

The pipeline converts your video's detected mood into ACE-Step's structured format:

- **Caption**: Dense natural-language prompt (e.g., "Gentle acoustic folk instrumental with fingerpicked guitar, soft brushed percussion..."). Full sentences with instrument descriptions work best.
- **Lyrics**: Section-tagged instrumental markers (`[Intro]\n[Instrumental]\n[Verse]\n[Instrumental]`)
- **BPM, key, time signature**: Sent as explicit API fields (not embedded in the caption text)
- **Instrumental flag**: Reinforced in every caption to prevent vocals

The pipeline detects each clip's mood and maps it to a genre template (lo-fi, acoustic, cinematic, etc.), so different video moods produce different music styles.
