---
sidebar_position: 9
title: Audio & Music
---

# Audio & Music

The music pipeline has three stages:

1. **Mood detection**: A vision LLM looks at keyframes from your video and outputs a structured mood analysis (happy, calm, energetic, etc. plus genre and tempo suggestions).
2. **Music generation**: The pipeline takes that mood and sends it to the configured music backend. ACE-Step can run directly in the app or through its REST API. MusicGen is the alternative generator when ACE-Step is disabled.
3. **Audio ducking**: When background music plays over your clips, it automatically gets quieter when someone's talking or when there's an interesting sound in the original audio.

## Music Providers

### ACE-Step

ACE-Step 1.5 generates higher-quality instrumental tracks than MusicGen. It supports explicit musical parameters (BPM, key, time signature) passed as structured API fields.

Two modes:

| Mode | How it works | When to use |
|------|-------------|-------------|
| `lib` | Direct Python import, in-process | Apple Silicon (MLX/MPS) or CUDA desktop, no server needed |
| `api` | Remote REST API server | Headless servers, Docker deployments, Python 3.13 |

Production model variants:

| Variant | DiT | Steps | Use |
|---------|-----|-------|-----|
| `turbo` | 2B | 8 | Fast preview on smaller machines |
| `base` | 2B | 50 | Special tasks and fine-tuning, not the normal soundtrack default |
| `acestep-v15-xl-turbo` | 4B | 8 | Recommended production soundtrack model on 20GB+ Apple Silicon/CUDA |
| `acestep-v15-xl-sft` | 4B | 50 | Maximum detail and tunable CFG; see the v0.1.8 warning below |
| `acestep-v15-xl-base` | 4B | 50 | Extract/lego/complete workflows, not needed for normal text-to-music |

The DiT model and LM planner are separate choices. `acestep-v15-xl-turbo` selects the 4B audio
executor; `lm_model_size: "4B"` selects the 4B planner. For local `lib` mode, use both for the
full XL setup. In `api` mode the remote ACE-Step server owns the loaded DiT/LM models, so
`model_variant` and `lm_model_size` in this app do not switch the server's models.

```yaml
ace_step:
  enabled: true
  mode: "lib"              # or "api"
  api_url: "http://localhost:8000"
  model_variant: "acestep-v15-xl-turbo"
  lm_model_size: "4B"
  bf16: true
  num_versions: 3
```

:::warning Python 3.12 or earlier required for local mode
ACE-Step local (`mode: "lib"`) requires Python 3.12 or earlier. API mode works on any Python version.
:::

:::warning ACE-Step v0.1.8 non-turbo XL models
v0.1.7 added DCW and enabled it by default. On v0.1.8, direct-library and REST callers still inherit
DCW-on for `xl-sft` and `xl-base`, which can produce garbled audio on Apple Silicon. The Gradio UI
has a model-aware default, but the equivalent CLI/API fix is still an
[open upstream change](https://github.com/ace-step/ACE-Step-1.5/pull/1282). Use `xl-turbo` for
production automation; test non-turbo XL only with DCW explicitly disabled in a patched server or
adapter.
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

### Local/API Fallback and Stem Separation

ACE-Step `lib` mode automatically falls back to the configured ACE-Step REST API when the local
package is not installed. It does not switch to MusicGen after an ACE generation failure.

When both providers are enabled, ACE-Step generates the track and MusicGen supplies remote Demucs
stem separation. With MusicGen disabled, an installed local Demucs handles stems instead. When
ACE-Step is disabled, MusicGen handles both generation and stems.

### Custom Music

You don't have to use AI-generated music. In the UI at Step 3, you can upload your own music file (MP3, WAV, FLAC, M4A, OGG). Or point at a directory:

```yaml
audio:
  music_source: "local"
  local_music_dir: "~/Music/Memories"
```

Or disable music entirely with `auto_music: false` or `--no-music`.

## Semantic Audio Events (Optional PANNs)

Audio-content analysis is separate from music generation and ducking. With PANNs installed, the
selector can label laughter, babies, speech, music, cheering, engines, and other AudioSet events.
Those labels help protect a laugh or spoken moment from a bad cut.

Install the optional backend with either package workflow:

```bash
uv sync --extra audio-ml
pip install 'immich-memories[audio-ml]'
```

Then enable it:

```yaml
audio_content:
  enabled: true
  use_panns: true
```

If Torch or PANNs is unavailable, generation does not fail. It uses the energy-only analyzer,
which can find loud and quiet structure but cannot reliably distinguish laughter from speech,
music, babies, or background noise. `immich-memories preflight` reports which backend is active.

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

On Apple Silicon with at least 20GB of unified memory, you can run the shown XL production profile
in-process. Lower-memory machines should use the 2B `turbo` profile instead:

```yaml
audio:
  auto_music: true
  music_source: "ace_step"

ace_step:
  enabled: true
  mode: "lib"
  model_variant: "acestep-v15-xl-turbo"
  lm_model_size: "4B"

musicgen:
  enabled: false           # Not needed: local Demucs handles stems
```

Install the tested ACE-Step 1.5 release into the same Python 3.12 environment as
`immich-memories`. ACE-Step's full UI dependency set currently conflicts with the app's Starlette
version, so install the pinned package without its UI/training dependencies, then add the direct
inference dependencies:

```bash
uv sync --extra demucs
uv pip install --python .venv/bin/python --no-deps \
  'ace-step @ git+https://github.com/ace-step/ACE-Step-1.5.git@v0.1.8'
uv pip install --python .venv/bin/python \
  'accelerate>=1.12.0' 'diffusers>=0.37.0' diskcache 'loguru>=0.7.3' \
  'mlx>=0.25.2' 'mlx-lm>=0.20.0' 'pytorch-wavelets>=1.3.0' \
  'pywavelets>=1.9.0' toml 'torchvision==0.25.0' \
  'transformers>=4.51.0,<4.58.0' 'typer-slim>=0.21.1' \
  'vector-quantize-pytorch>=1.27.15'
```

The command above is the tested Apple Silicon inference installation; it deliberately does not
install ACE-Step's Gradio UI. CUDA hosts should use the pinned v0.1.8 release with the appropriate
PyTorch wheels. `uv sync --inexact` preserves this manual installation. An exact `uv sync` removes
packages not declared by this project, so rerun the ACE-Step commands afterward.

### Memory on Apple Silicon

In `lib` mode the app caps ACE-Step's MLX memory before loading models: the VAE decodes audio in
~10 s chunks (`ACESTEP_MLX_VAE_CHUNK=256`) and the MLX buffer cache is limited to 4 GiB. Without
this, ACE-Step's own heuristic picks an 82 s decode chunk on Macs with more than 64 GB and the
process footprint grows by roughly 0.8 GiB per second of audio in that chunk — a 216 s track hit
108 GB and macOS killed the UI. With the cap the same track peaks around 53 GB for the XL/4B profile
(most of that is model weights) at a ~20% slower VAE decode. Set `ACESTEP_MLX_VAE_CHUNK` yourself
to override the chunk size; ACE-Step's `ACESTEP_SAVE_MEMORY` and `MAX_MPS_VRAM` do not bound this
allocation.

The MLX DiT copy runs in bf16 — the same precision ACE-Step uses on CUDA — instead of the fp32
ACE-Step converts it from on macOS (7.8 GB instead of 15.5 GB for the XL model). Set
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32=1` to keep fp32. Once a music batch finishes, the models are
dropped and both torch's and MLX's caches are released, so the process falls back to ~1 GB between
generations instead of holding ~27 GB of parked GPU memory; the next batch reloads the models
(~25 s).

For a hosted generator, leave ACE-Step out of the app environment and use `mode: "api"` with the
server URL. For a desktop that normally runs locally but has a server available as backup, keep
`mode: "lib"` and set `api_url`; the app uses the API only when the local package is unavailable.

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

**`ducking_ratio` (6.0)**: how much the volume drops when ducking activates. A ratio of 6.0 means a large dip. Lower values (e.g., 3.0) give a subtler dip.

**`music_volume_db` (-6.0)**: the baseline music volume *before* any ducking. At -6 dB, the music is already mixed quieter than the clip audio.

**`fade_in_seconds` (2.0)** / **`fade_out_seconds` (3.0)**: how quickly the music volume transitions. These are the global fade at the start and end of the video, not per-clip ducking fades.

## Model Cache & Disk Usage

| Model | Cache Location | Size | When Downloaded |
|-------|---------------|------|----------------|
| ACE-Step turbo/base (2B) | `~/.cache/ace-step/checkpoints/` | ~4.5 GB each | First generation |
| ACE-Step XL-turbo (4B) | `~/.cache/ace-step/checkpoints/` | ~19 GB observed | First generation |
| ACE-Step LM 0.6B | `~/.cache/ace-step/checkpoints/` | ~1.2 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 1.7B | `~/.cache/ace-step/checkpoints/` | ~3.4 GB | First generation (if `use_lm: true`) |
| ACE-Step LM 4B | `~/.cache/ace-step/checkpoints/` | ~7.8 GB observed | First generation (if `use_lm: true`) |
| Shared ACE VAE + embedding | `~/.cache/ace-step/checkpoints/` | ~1.4 GB observed | First generation |
| Demucs htdemucs | `~/.cache/torch/hub/` | ~80 MB | First stem separation |

**Total disk for the XL production profile** (XL-turbo + 4B LM + shared assets + Demucs): about
28 GB on the tested v0.1.8 installation. Old 2B checkpoints are not removed automatically.
