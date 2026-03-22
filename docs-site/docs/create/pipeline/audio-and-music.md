---
sidebar_position: 9
title: Audio & Music
---

# Audio & Music

The music pipeline has three stages:

1. **Mood detection**: A vision LLM looks at keyframes from your video and outputs a structured mood analysis (happy, calm, energetic, etc. plus genre and tempo suggestions).
2. **Music generation**: The multi-provider pipeline takes that mood and sends it to a music generation backend. It tries backends in priority order, ACE-Step first, falls back to MusicGen if unavailable or fails.
3. **Audio ducking**: When background music plays over your clips, it automatically gets quieter when someone's talking or when there's an interesting sound in the original audio.

## Music Providers

### ACE-Step

ACE-Step 1.5 generates higher-quality instrumental tracks than MusicGen. It supports explicit musical parameters (BPM, key, time signature) passed as structured API fields.

Two modes:

| Mode | How it works | When to use |
|------|-------------|-------------|
| `lib` | Direct Python import, in-process | Apple Silicon / CUDA desktop, no Docker needed |
| `api` | Remote REST API server | Headless servers, Docker deployments, Python 3.13 |

Two model variants:

| Variant | Steps | Speed (60s track, M5 Max) | Quality |
|---------|-------|--------------------------|---------|
| `turbo` | 8 | ~8s | Fast preview, synthetic timbres |
| `base` | 60 | ~40s | Production quality, natural instruments |

```yaml
ace_step:
  enabled: true
  mode: "lib"              # or "api"
  api_url: "http://localhost:8000"
  model_variant: "base"    # or "turbo" for fast previews
  bf16: true
  num_versions: 3
```

:::warning Python 3.12 or earlier required for local mode
ACE-Step local (`mode: "lib"`) requires Python 3.12 or earlier. API mode works on any Python version.
:::

### MusicGen

Meta's MusicGen handles text-to-music generation and Demucs stem separation via a remote API server. If you're running everything locally with ACE-Step + local Demucs, you don't need MusicGen at all.

```yaml
musicgen:
  enabled: true
  base_url: "http://localhost:8000"
  timeout_seconds: 10800         # 3 hours max per job
  num_versions: 3
```

### Multi-Provider Setup

The ideal setup runs both backends. ACE-Step handles music generation (better quality), MusicGen handles Demucs stem separation.

The pipeline tries backends in order:
1. Check if ACE-Step is available (health check)
2. If yes, generate with ACE-Step
3. If ACE-Step fails or is unreachable, fall back to MusicGen
4. Regardless of which backend generated, use Demucs for stem separation

### Custom Music

You don't have to use AI-generated music. In the UI at Step 3, you can upload your own music file (MP3, WAV, FLAC, M4A, OGG). Or point at a directory:

```yaml
audio:
  music_source: "local"
  local_music_dir: "~/Music/Memories"
```

Or disable music entirely with `auto_music: false` or `--no-music`.

## Audio Ducking

When background music plays over your clips, it should get quieter when someone's talking or when there's an interesting sound in the original audio. The music automatically dips to let the original audio through, then comes back up.

### How it works

1. **Stem separation**: [Demucs](https://github.com/facebookresearch/demucs) splits the clip's audio into vocals and non-vocal stems
2. **Activity detection**: when the vocal/sound energy exceeds the ducking threshold, the music volume drops
3. **Smooth transitions**: fade in/out prevents jarring volume jumps

### Demucs dependency

Stem separation requires [Demucs](https://github.com/facebookresearch/demucs), which downloads a model on first use (~80 MB). If Demucs isn't available, ducking still works but uses simpler energy detection on the mixed audio, which is less accurate at distinguishing speech from music.

Install locally: `pip install 'immich-memories[demucs]'` and the pipeline auto-detects it. Or use MusicGen's remote `/separate` endpoint.

## Fully Local Setup (No Servers)

On Apple Silicon with enough memory (16GB+), you can run the entire pipeline in-process:

```yaml
audio:
  auto_music: true
  music_source: "ace_step"

ace_step:
  enabled: true
  mode: "lib"
  model_variant: "base"
  lm_model_size: "4B"

musicgen:
  enabled: false           # Not needed: local Demucs handles stems
```

Install the packages:

```bash
pip install 'immich-memories[demucs]'
pip install 'ace-step @ git+https://github.com/ace-step/ACE-Step.git'
pip install 'torchcodec>=0.1'
```

## Configuration

```yaml
audio:
  auto_music: false
  music_source: "musicgen"       # local, musicgen, or ace_step
  local_music_dir: "~/Music/Memories"
  ducking_threshold: 0.02        # Voice detection sensitivity (0-1)
  ducking_ratio: 6.0             # How much to lower music (1-20)
  music_volume_db: -6.0          # Base music volume (-20 to 0 dB)
  fade_in_seconds: 2.0           # Music fade in (0-10s)
  fade_out_seconds: 3.0          # Music fade out (0-10s)
```

### Key parameters

**`ducking_threshold` (0.02)**: the minimum audio energy in the clip that triggers ducking. Lower values make it more sensitive (music ducks for quieter sounds). If your clips have a lot of background noise, you might want to raise this to 0.05 or higher.

**`ducking_ratio` (6.0)**: the amount of volume reduction when ducking activates. A ratio of 6.0 means the music drops significantly. Lower values (e.g., 3.0) give a subtler dip.

**`music_volume_db` (-6.0)**: the baseline music volume *before* any ducking. At -6 dB, the music is already mixed quieter than the clip audio.

**`fade_in_seconds` (2.0)** / **`fade_out_seconds` (3.0)**: how quickly the music volume transitions. These are the global fade at the start and end of the video, not per-clip ducking fades.

## Model Cache & Disk Usage

| Model | Cache Location | Size | When Downloaded |
|-------|---------------|------|----------------|
| ACE-Step turbo | `~/.cache/huggingface/hub/` | ~2 GB | First generation |
| ACE-Step base | `~/.cache/huggingface/hub/` | ~2 GB | First generation |
| ACE-Step LM 0.6B | `~/.cache/huggingface/hub/` | ~1.2 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 1.7B | `~/.cache/huggingface/hub/` | ~3.4 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 4B | `~/.cache/huggingface/hub/` | ~8 GB | First generation (if `use_lm: true`) |
| Demucs htdemucs | `~/.cache/torch/hub/` | ~80 MB | First stem separation |

**Total disk for a full local setup** (base + 4B LM + Demucs): ~10 GB of cached models.
