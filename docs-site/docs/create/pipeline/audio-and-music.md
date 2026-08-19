---
sidebar_position: 9
title: Audio & Music
---

# Audio & Music

The music pipeline has three stages:

1. **Mood detection**: A vision LLM looks at keyframes from your video and outputs a structured mood analysis (happy, calm, energetic, etc. plus genre and tempo suggestions).
2. **Music generation**: The pipeline takes that mood and sends it to the configured music backend. ACE-Step can run directly in the app or through its REST API. MusicGen is the alternative generator when ACE-Step is disabled.
3. **Audio ducking**: When background music plays over your clips, it automatically gets quieter when someone's talking or when there's an interesting sound in the original audio.

## No GPU? Start here

A plain install produces silent videos unless you supply an MP3 per run: both
music generators need a GPU or a separate server. The `music` extra ships 28
royalty-free background tracks that are used automatically when no generator is
configured, so Docker and NAS installs have music out of the box.

The Docker image already includes it.

Installing it yourself needs the package from this repository, since it is not
published to PyPI:

```bash
pip install "immich-memories-music @ git+https://github.com/sam-dumont/immich-video-memory-generator.git#subdirectory=packages/immich-memories-music"
```

It is deliberately left out of the `all` extra so that
`pip install "immich-memories[all]"` stays resolvable from PyPI.

Tracks cover five moods (calm, energetic, happy, nostalgic, tender) in acoustic
and electronic styles, roughly 30 seconds each, and are repeated with a crossfade
to fill longer videos. Selection follows the memory's detected mood, and a mood
with no bundled folder falls back to another track rather than to silence.

They were generated locally with ACE-Step 1.5 — nothing sampled from or derived
from third-party recordings, so there is no attribution requirement. The models,
settings and per-track tempo, key and seed are recorded in `LICENSE-MUSIC` inside
the package.

Supplying `--music yourfile.mp3` or configuring a generator overrides the bundle;
`--no-music` still means no music.

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

You don't have to use AI-generated music. In the UI at Step 3, choose **Upload file** under **Background music** (MP3, M4A or WAV) and set the **Music volume** slider. On the CLI, pass `--music /path/to/track.mp3` (and `--music-volume 0.0-1.0`, default 0.5).

To disable music, choose **None** in the UI or pass `--no-music` on the CLI. Without `--music`, the CLI generates an AI track when `ace_step.enabled` or `musicgen.enabled` is set, and otherwise renders with the clips' own audio.

For a local library, `immich-memories music search` and `music add` read `audio.local_music_dir` (default `~/Music/Memories`); see the [music command](../cli/music.md). Generation itself does not pick from that directory.

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
ace_step:
  enabled: true
  mode: "lib"
  model_variant: "acestep-v15-xl-turbo"
  lm_model_size: "4B"
  use_lm: false            # See "Thinking mode" below

musicgen:
  enabled: false           # Not needed: local Demucs handles stems
```

### Thinking mode (`use_lm`)

Off by default. When on, ACE-Step's 5Hz language model rewrites your caption and
invents its own genre metadata before the audio model ever sees the prompt, which
pulls instrumental briefs off-target. It also dominates generation time — a 60 s
track took ~45 s with it on and ~17 s with it off.

Turn it on only if you want the model to elaborate a vague brief. The music
prompts this project ships are already written the way ACE-Step's own guides
recommend (genre first, then mood, instruments, production tags and BPM), so
they do not need rewriting.

:::note Upgrading
`use_lm` previously defaulted to `true`. If your `config.yaml` sets it
explicitly, set it to `false` to pick up the improved output.
:::

Install the tested ACE-Step 1.5 release into the same Python 3.12 environment as
`immich-memories`. ACE-Step's full UI dependency set currently conflicts with the app's Starlette
version, so install the pinned package without its UI/training dependencies, then add the direct
inference dependencies:

```bash
uv sync --extra demucs
make install-acestep
```

`make install-acestep` runs the pinned commands below and then imports the backend to
prove the install actually works — a mismatched `torchvision` fails only at model load,
several minutes into a generation, with `operator torchvision::nms does not exist`.

<details>
<summary>What the target runs</summary>

```bash
uv pip install --python .venv/bin/python --no-deps \
  'ace-step @ git+https://github.com/ace-step/ACE-Step-1.5.git@v0.1.8'
uv pip install --python .venv/bin/python \
  'accelerate>=1.12.0' 'diffusers>=0.37.0' diskcache 'loguru>=0.7.3' \
  'mlx>=0.25.2' 'mlx-lm>=0.20.0' 'pytorch-wavelets>=1.3.0' \
  'pywavelets>=1.9.0' toml 'torchvision==0.25.0' \
  'transformers>=4.51.0,<4.58.0' 'typer-slim>=0.21.1' \
  'vector-quantize-pytorch>=1.27.15'
```

</details>

The command above is the tested Apple Silicon inference installation; it deliberately does not
install ACE-Step's Gradio UI. CUDA hosts should use the pinned v0.1.8 release with the appropriate
PyTorch wheels. The `make` quality gates sync with `--inexact`, so they leave this installation alone.
A bare `uv sync` is exact and removes packages this project does not declare, so rerun
`make install-acestep` afterwards if you run one.

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

Which music plays is decided by three switches:

| Where | Switch | Effect |
|-------|--------|--------|
| Config | `ace_step.enabled` / `musicgen.enabled` | When either is true, generation produces an AI track by default (ACE-Step first when both are on) |
| CLI | `--music PATH`, `--no-music`, `--music-volume 0.0-1.0` | Own file, no music at all, or the mix level (default 0.5) |
| UI Step 3 | **Background music**: None / Upload file / AI Generated, plus the volume slider | Same choices per run |

The music volume slider maps to a base music level of −20 dB (0.0) to 0 dB (1.0) before ducking. Ducking parameters are fixed in the mixer (sidechain threshold 0.02, ratio 4.0, 100 ms attack, 2.5 s release, 2 s fade in, 3 s fade out). The `audio:` section in the schema (`auto_music`, `music_source`, `ducking_threshold`, `ducking_ratio`, `music_volume_db`, `fade_in_seconds`, `fade_out_seconds`) is not read by generation; only `audio.local_music_dir` is used, by the `music` command. If you need custom fades or a dB level, run `immich-memories music add` on the finished file with `--volume`, `--fade-in`, `--fade-out`.

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
