---
sidebar_position: 9
title: Audio & Music
---

# Audio & Music

Three stages: a mood for the memory, a track for the mood, and ducking so the music drops under
the clips' own sound. Ducking is a sidechain compressor keyed on the clip's audio; it does not
know what the sound is, so speech, laughter, wind and traffic all duck it.

## Which music plays

| Where | Switch | Effect |
|---|---|---|
| Config | `ace_step.enabled`, `musicgen.enabled` | When either is on, a track is generated (ACE-Step first when both are) |
| CLI | `--music PATH`, `--no-music`, `--music-volume 0.0-1.0` (default 0.5) | Your file, no music, or the level |
| UI, Generation Options | **Background music**: None, Upload file, Bundled, AI Generated, plus the volume slider | Same choices per run |

The chain is fixed: an explicit file wins; otherwise a generator if one is enabled; otherwise a
bundled track. A generator that fails falls through to the next, then to the bundle, and the run
is told: the substitution comes back as a warning on the finished video and in the nightly
notification, so a dead backend does not sound like working music forever.

## The bundled tracks

A plain install renders silent videos unless you supply a file. The `music` extra ships 28
royalty-free tracks (the Docker image and the `all` extra include it), used when no generator is
configured:

```bash
pip install "immich-memories[music]"
```

Five moods (calm, energetic, happy, nostalgic, tender) in acoustic and electronic styles, about
30 s each, looped with a crossfade to fill longer videos. They were generated locally with
ACE-Step 1.5 from nothing sampled, so there is no attribution requirement; the settings and each
track's tempo, key and seed are in `LICENSE-MUSIC` inside the package. The pick follows the
memory's mood when the pipeline has one; today the per-clip emotion field the mood aggregation
reads is not written by anything, so the choice is whole-library and random.

When a memory holds photos, a bundled track whose measured beat lands within 0.2 beats of the
photo cadence is preferred, so cuts land with the pulse. The window is loose on purpose: the
detector quantises the beat period to 23 ms frames and reads half or double time often enough
that a tighter window would throw away tracks that fit.

## ACE-Step

ACE-Step 1.5 takes explicit musical parameters (BPM, key, time signature) as structured fields.
Two modes:

| `ace_step.mode` | What runs |
|---|---|
| `api` (default) | HTTP to an ACE-Step server, polled every 3 s. The server owns the loaded models; `model_variant` and `lm_model_size` here do not switch them |
| `lib` | The model in this process: MLX on Apple Silicon, CUDA on NVIDIA, PyTorch CPU otherwise. Python 3.12 or earlier. Falls back to `api` when the package is missing |

A worked example, not the defaults. Shipped, `enabled` is `false`, `mode` is `api`,
`model_variant` is `turbo` (the 2B family) and `lm_model_size` is `1.7B`:

```yaml
ace_step:
  enabled: true
  mode: "lib"
  api_url: "http://localhost:8000"
  model_variant: "acestep-v15-xl-turbo"   # 4B, 8 steps: the production soundtrack model
  lm_model_size: "4B"
  use_lm: false
  num_versions: 3
```

The variants: `turbo` and `base` (2B, 8 and 50 steps), `acestep-v15-xl-turbo` (4B, 8 steps),
`acestep-v15-xl-sft` and `acestep-v15-xl-base` (4B, 50 steps, for tuning and extract workflows).
On v0.1.8 the non-turbo XL models inherit DCW on and can produce garbled audio on Apple Silicon;
use `xl-turbo` for automation.

`use_lm` is off by default: with it on, ACE-Step's language model rewrites the caption before the
audio model sees it, pulls instrumental briefs off target, and takes a 60 s track from about 17 s
to 45 s. The prompts this project ships are already written the way ACE-Step's guides recommend.

When the memory holds photos, the requested tempo is nudged so a photo lasts a whole number of
beats, measured against the interval between visible cuts (3.5 s at the default 4 s photo and
0.5 s crossfade), within the genre's tempo range and within 15 % of the mood's tempo. Videos are
never re-timed.

### Running it in-process on Apple Silicon

`lib` mode checks free memory against the weights the profile keeps resident and refuses with a
named shortfall rather than letting macOS kill the process mid-render: about 29 GB for XL with the
4B planner, 21 GB for XL without it, 11 GB and 7 GB for the 2B profiles. A refusal is a normal
backend failure: MusicGen next, then a bundled track. The MLX buffer cache is capped at 4 GiB and
the DiT copy runs in bf16 (7.8 GB instead of 15.5 GB for XL); set
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32=1` to keep fp32. Models are dropped after each batch, so the
process falls back to about 1 GB between generations.

Install the pinned release into the app's Python 3.12 environment without its UI dependencies:

```bash
uv sync --extra demucs
make install-acestep
```

The target runs the pinned `uv pip install` lines and imports the backend to prove the install
works (a mismatched torchvision fails only at model load, minutes into a generation). A bare
`uv sync` removes what the project does not declare; rerun `make install-acestep` after one.

## MusicGen

Meta's MusicGen through a remote server, for text-to-music and for Demucs stem separation:

```yaml
musicgen:
  enabled: true
  base_url: "http://localhost:8000"
  timeout_seconds: 10800
  num_versions: 3
```

With ACE-Step enabled, MusicGen is the fallback. With it disabled, MusicGen generates alone.

## Ducking and stems

The CLI masters the full mix and ducks that with a sidechain compressor (threshold 0.02, ratio
4.0, 100 ms attack, 2.5 s release, 2 s fade in, 3 s fade out); `--music-volume` maps 0.0 to
1.0 onto −20 dB to 0 dB before ducking. The web UI's mixer is a different path: it separates the
clip audio into stems with [Demucs](https://github.com/facebookresearch/demucs) and ducks the
four stems independently (slider −40 dB to 0 dB, default 0.7, ratio 6.0, 50 ms attack, 500 ms
release). Demucs comes from `pip install 'immich-memories[demucs]'` (an 80 MB model on first use)
or from MusicGen's remote `/separate` endpoint; without either, ducking uses plain energy
detection on the mixed audio. The CLI asks for no separation.

None of the ducking constants has a config key; `audio:` holds only `local_music_dir`
(`~/Music/Memories`), which the [`music` command](../cli/music.md) reads. For custom fades or a
dB level, run `immich-memories music add` on the finished file.

## Disk

| Model | Location | Size |
|---|---|---|
| ACE-Step 2B (turbo, base) | `~/.cache/ace-step/checkpoints/` | about 4.5 GB each |
| ACE-Step XL-turbo (4B) | same | about 19 GB |
| ACE-Step planners (0.6B, 1.7B, 4B) | same | 1.2, 3.4, 7.8 GB |
| Shared VAE and embedding | same | about 1.4 GB |
| Demucs htdemucs | `~/.cache/torch/hub/` | about 80 MB |

The XL production profile with the 4B planner is about 28 GB on disk. Old checkpoints are not
removed automatically.
