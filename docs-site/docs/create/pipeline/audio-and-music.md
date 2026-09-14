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

A plain install adds no soundtrack unless you supply or generate one; source clip audio remains.
The `music` extra ships 28
royalty-free tracks (the Docker image and the `all` extra include it), used when no generator is
configured:

```bash
pip install "immich-memories[music]"
```

Five moods (calm, energetic, happy, nostalgic, tender) in acoustic and electronic styles, about
30 s each, looped with a crossfade to fill longer videos. The package releases them under the MIT license. `LICENSE-MUSIC` records their local
ACE-Step 1.5 generation settings and each track's tempo, key and seed. The pick follows the
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

```yaml
ace_step:
  enabled: true
  mode: "lib"
  api_url: "http://localhost:8000"
  model_variant: "acestep-v15-xl-turbo"   # 4B, 8 steps; an XL example, not the default
  lm_model_size: "4B"
  use_lm: false
  num_versions: 3
```

The variants: `turbo` and `base` (2B, 8 and 50 steps), `acestep-v15-xl-turbo` (4B, 8 steps),
`acestep-v15-xl-sft` and `acestep-v15-xl-base` (4B, 50 steps, for tuning and extract workflows).
The default variant is `turbo`; the example above selects the larger XL model.

`use_lm` is off by default. Turning it on lets ACE-Step's language model rewrite the
music brief and adds memory use and generation work. It can also change the requested genre.

When the memory holds photos, the requested tempo is nudged so a photo lasts a whole number of
beats, measured against the interval between visible cuts (3.5 s at the default 4 s photo and
0.5 s crossfade), within the genre's tempo range and within 15 % of the mood's tempo. Videos are
never re-timed.

### Running it in-process on Apple Silicon

`lib` mode checks available memory before loading: 29 GiB for XL with the 4B planner,
21 GiB for XL without it, 11 GiB for 2B with the 1.7B planner, or 7 GiB for 2B without it.
These are admission floors based on weights, not peak-memory guarantees. Leave room for
generation, FFmpeg and other services. A refusal falls through to MusicGen, then a bundled
track when available.

The MLX buffer cache is capped at 4 GiB. Models are released after each batch, but actual
process memory depends on the libraries and other work sharing the process.

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

CLI generation and web UI export use the shared soundtrack mixer. It masters the music
and ducks the full track against the source audio. `--music-volume` maps 0.0–1.0 onto
−20 dB–0 dB before ducking; use `--no-music` to omit the soundtrack.

The UI's music-preview generator can separate the generated soundtrack into stems using
Demucs or MusicGen's remote separation endpoint. It does not separate your source clips,
and ordinary final export does not require stems.

None of the ducking constants has a config key; `audio:` holds only `local_music_dir`
(`~/Music/Memories`), which the [`music` command](../cli/music.md) reads. For custom fades or a
dB level, run `immich-memories music add` on the finished file.

## Model storage

In-process ACE-Step stores checkpoints under `~/.cache/ace-step/checkpoints/`. Demucs uses
its Torch model cache under `~/.cache/torch/hub/`. These sit outside the app's media-cache
budget. Changing a model does not automatically remove older checkpoints, so check these
directories when accounting for disk use.
