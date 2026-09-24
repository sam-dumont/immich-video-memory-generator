---
title: Generated music
---

# Generated music

Reader: power user.

Every film already gets music on a plain NAS: your own file, or one of 28 bundled tracks picked by
the film's mood and ducked under the clips' own sound
([Music](../make/titles-maps-music.md#music)). A generator writes an original track for each film
instead, in the mood, tempo and length the film asks for. Two are supported: ACE-Step, local or
behind an API, and MusicGen behind a server. Nothing here is on by default, and a generator that
fails falls through to a bundled track with a warning on the finished film, so a dead backend never
passes for working music.

## ACE-Step

ACE-Step 1.5 takes the tempo, key and time signature as structured fields. Two modes:

- `api` (the default): HTTP to an ACE-Step server, polled every 3 s. The server owns the models, so
  the app's box needs nothing.
- `lib`: the model runs in the app's own process: MLX on Apple Silicon, CUDA on NVIDIA, CPU
  otherwise. It needs Python 3.12 and falls back to `api` when the package is missing.

A worked example, not the defaults (those are `enabled: false`, `mode: api`, `model_variant: turbo`,
`lm_model_size: 1.7B`):

```yaml
advanced:
  ace_step:
    enabled: true
    mode: lib
    model_variant: acestep-v15-xl-turbo   # 4B, 8 steps
    lm_model_size: 4B
    use_lm: false
    num_versions: 1
  musicgen:
    enabled: false                        # local Demucs does the stems
```

The variants are `turbo` and `base` (2B, 8 and 50 steps), `acestep-v15-xl-turbo` (4B, 8 steps), and
`acestep-v15-xl-sft` and `-base` (4B, 50 steps). On v0.1.8 the non-turbo XL models can produce
garbled audio on Apple Silicon, so use `xl-turbo` for anything unattended. `use_lm` stays off: with
it on, ACE-Step's language model rewrites the brief before the audio model sees it, pulls
instrumental briefs off target, and takes a 60 s track from about 17 s to 45 s.

### Memory and disk

`lib` mode checks free memory against the weights the profile has to keep resident, and refuses
with a named shortfall rather than letting macOS kill the process mid-render. A refusal is an
ordinary backend failure: MusicGen is next, then a bundled track.

| Profile | Resident weights it needs free | On disk |
|---|---|---|
| XL (4B) with the 4B planner | about 29 GB | about 28 GB |
| XL (4B), `use_lm: false` | about 21 GB | about 20 GB |
| 2B with the 1.7B planner | about 11 GB | about 9 GB |
| 2B, `use_lm: false` | about 7 GB | about 6 GB |

Per file, under `~/.cache/ace-step/checkpoints/`: the 2B models about 4.5 GB each, XL-turbo about
19 GB, the planners 1.2, 3.4 and 7.8 GB (0.6B, 1.7B, 4B), the shared VAE and embedding about
1.4 GB. Demucs' htdemucs is about 80 MB under `~/.cache/torch/hub/`. Old checkpoints are never
removed for you.

A full XL render with the 4B planner peaks around 53 GB of unified memory, most of it cache the OS
takes back under pressure, which is why the check tests the weights and not the peak. The MLX
buffer cache is capped at 4 GiB and the DiT runs in bf16 (7.8 GB instead of 15.5 GB for XL;
`IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32=1` keeps fp32). A local reader holding its 17 GB is often what
stops XL fitting, so stop the model server before a music-heavy run.

### Install locally on a Mac

`lib` mode is not in `uv tool install` or the `all-mac` extra. From a checkout:

```bash
brew install uv ffmpeg
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev-mac
make install-acestep
make check-local-audio          # AUDIO_CHECK_ARGS="--quality high" tries XL-turbo
uv run immich-memories ui
```

`make install-acestep` installs ACE-Step v0.1.8 and Demucs into a sibling `.venv-acestep`, because
ACE-Step's Transformers pin wants an older Hugging Face library than the editor. `make
check-local-audio` generates 15 seconds, splits all four stems and fails loudly if any of it didn't
happen locally, so a remote server or a bundled track can't pass it. Rerun the installer after
moving the checkout or changing the app version; it also repairs
`operator torchvision::nms does not exist`. A bare `uv sync` can remove Demucs from the editor's
environment, and the installer puts it back.

### In a container

A separate ACE-Step container serves the editor with `mode: api` and `api_url` pointing at it; keep
its model cache on a volume. On an NVIDIA box that is the way to go. On a Mac, a container can't
reach Metal, so run ACE-Step natively and point the app in Docker at
`http://host.docker.internal:8000`.

## MusicGen

Meta's MusicGen, through a remote server only: text-to-music, and Demucs stem separation on its
`/separate` endpoint. With ACE-Step enabled it is the fallback; alone, it generates.

```yaml
advanced:
  musicgen:
    enabled: true
    base_url: "http://musicgen-server:8000"
    timeout_seconds: 10800
    num_versions: 3
```

## What generation adds to the mix

- **Tempo fits the photos.** In a film with photos, the tempo is nudged so a photo lasts a whole
  number of beats, within the genre's range and 15 % of the mood's tempo. Videos are never
  re-timed.
- **A stuck loop is re-rolled.** Each take is checked for a metronomic, repetitive grid, and a
  flagged one is replaced by up to `audio.max_regenerations` (2) more takes, keeping the best.
  Music is never dropped for it.
- **Long films chain takes.** Past `audio.music_block_seconds` (120), up to
  `audio.max_music_blocks` (3) distinct takes are crossfaded and looped, rather than one long
  generation.
- **Four stems.** The track is split with Demucs: vocals duck most under the clips' sound, drums
  keep their rhythm. Local Demucs uses Metal on Apple Silicon (`immich-memories[demucs]` alone);
  a MusicGen server's `/separate` wins when MusicGen is on.
