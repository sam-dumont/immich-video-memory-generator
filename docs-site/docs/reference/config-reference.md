---
title: Config Reference
sidebar_label: Config Reference
---

# Config Reference

Every key with its built-in default. Add the ones you want to `~/.immich-memories/config.yaml`.

:::tip Config tiers
Tier 2 sections (`analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`,
`automation`, `notifications`, `triage`, `editorial`, `inference`) go under an `advanced:` key when
the app saves the file:

```yaml
advanced:
  analysis:
    max_album_assets: 5000
  hardware:
    encoder_preset: "quality"
```

Both placements are read; if a section appears in both, the top-level one wins. Everything else
stays top level. Unknown keys inside a section are ignored; unknown top-level keys and invalid
values fail validation at startup.
:::

## Preset

One top-level switch that fills several knobs at once. `fast` is the CPU-only / NAS profile.
Anything you set yourself wins over the preset.

```yaml
preset: null                       # null | fast
```

`fast` sets, unless you set them yourself: `output.resolution: 1080p`, `output.codec: h264`,
`output.quality: fast`, `hardware.encoder_preset: fast` and
`title_screens.animated_background: false` (static title backgrounds). Music generation is already
off by default and stays wherever you put it.

Env: `IMMICH_MEMORIES_PRESET=fast`. One-off on the CLI: `immich-memories --preset fast generate …`
(root option, before the subcommand).

Caveat: the Memory page's **Save Config** (under Advanced) writes every value to `config.yaml`, not
just the connection fields it appears to be about. They then all count as set by you and the preset
has nothing left to fill in. Remove the keys you want the preset to own again. `server.host` is the
single exception; see [Server (UI)](#server-ui).

## Immich connection

Immich Memories supports **Immich v2 and v3**. Automatic runtime detection is the default:

```yaml
immich:
  url: "https://photos.example.com"
  api_key: "${IMMICH_API_KEY}"
  api_version: auto  # auto | v2 | v3
```

Keep `api_version` on `auto` for normal use. The client detects and caches the server major for
each runtime client; you do not choose it for each generation. Explicit `v2` or `v3` is a manual
troubleshooting escape hatch for a proxy or unusual deployment that prevents correct detection.
An override forces that API contract.

Run the read-only `immich-memories config test` to check credentials and see the resolved API
contract without generating or uploading a memory.

## Render worker

The CLI and web UI can send an already selected film to a trusted render worker.
Blank `worker_base_url` renders on the app's machine.

```yaml
render:
  worker_base_url: ""
  worker_token: ""             # Or ${RENDER_WORKER_TOKEN}
  allow_insecure_http: false   # Explicitly accept a non-loopback cleartext HTTP worker
  timeout_seconds: 3600        # Wait for rendering and download; maximum 86400
  fallback_to_local: false     # Explicitly allow local rendering after a worker failure
```

Use the same app version on both machines. The worker receives the selected assets,
exact cuts, Live source material, titles, locations, audio markers and the Immich API key
so it can download the sources directly. Configure a worker you trust, reachable over
your private network or HTTPS. The handoff request carries that Immich key, so a
non-loopback `http://` worker URL is refused until `allow_insecure_http: true` says you
meant it; loopback addresses and HTTPS need no opt-in. Preflight names the transport
before the first render request. Requests require the worker token and do not follow redirects.

Output is H.264 or H.265 MP4. MOV and ProRes use local rendering. Orientation only sets
the canvas; it does not change the selection. Speech detection and cut selection run
before handoff. Music and Immich upload finish on the app after it checks the returned film.

See [worker deployment](https://github.com/sam-dumont/immich-video-memory-generator/tree/main/services/render-worker)
for Docker Compose and Kubernetes examples.

## Video analysis

```yaml
analysis:
  # Media the camera roll did not shoot (see Configuration → Footage the
  # camera roll did not shoot). Setting the list replaces it; [] turns it off.
  exclude_filename_patterns:     # case-insensitive globs on the source filename
    - "RingVideo_*"
    - "RPReplay_Final*"
    - "Screen Recording *"
    - "Screenshot*"
    - "img-*-wa[0-9][0-9][0-9][0-9]*"
    - "vid-*-wa[0-9][0-9][0-9][0-9]*"
  exclude_stills_without_camera_exif: true   # a photo naming no camera was received, not shot
  min_source_short_side: 1080    # Drop smaller clips unless they carry camera EXIF
  max_source_video_seconds: 300  # Exclude longer source videos on Immich metadata, before download (0 disables)

  # Album source
  max_album_assets: 10000        # Most assets read from one album, per media type (min 1)

  # Downloads
  download_workers: 3            # Parallel download clients for video and thumbnail prefetching (1-8)

  # Duration sizing
  optimal_clip_duration: 5.0     # Expected seconds per clip when a trip or album sizes its own duration (2-15s)

  # Live Photos (iPhone 3s video clips)
  include_live_photos: true      # Include Live Photo clips (ON by default)
  live_photo_merge_window_seconds: 10.0  # Max gap to group as burst (1-60s)
  live_photo_min_clip_seconds: 3.5       # Below this a burst ships as a photo (0-30s)
```

Any Live Photo cluster of two or more within the merge window is treated as a burst; the count is
not configurable. Where a clip is cut, and how long it runs, is the editor's decision per carrier.

`max_album_assets` applies per media type, so the default reads up to 10,000 videos and 10,000
photos from one album. Immich returns newest first, so a bigger album is truncated to its most
recent assets, with a warning naming it.

## Speech boundaries

```yaml
advanced:
  speech:
    enabled: true          # Move video cuts out of detected speech
    vad_threshold: 0.25    # Voice probability threshold (0.1-0.9)
    min_silence_ms: 200    # Pause that separates utterances (50-2000ms)
```

The bundled FireRedVAD model runs locally with the `editorial` or `editorial-cuda` extra.
It measures retained videos and Live Photo companions, then maps speech onto the stitched
timeline. The editor fits the resulting intervals before rendering; an uninterrupted
utterance may cost more time or cause a clip to be left out. This detects voice activity,
not sentence meaning. Music ducking remains separate.

## Generation defaults

```yaml
defaults:
  scale_mode: "blur"             # blur | fit (black bars); used when --scale-mode is not given
  transition: "smart"            # cut, crossfade, smart, none (used when --transition is left on smart)
  transition_duration: 0.5       # 0-2 seconds
```

Target duration and orientation are per run (`--duration`, `--orientation`, or the UI), with the
memory type preset supplying the default duration; there is no config default for either. The
target covers the finished video, title and ending cards included, and the encoder lands near it
rather than exactly on it. There is no backfill: if the stories the editor funded do not fill the
budget, the run reports the shortfall.

## Output

```yaml
output:
  directory: "~/Videos/Memories"
  format: "mp4"                  # mp4 or mov
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: h264                     # h264 (default), h265 (HDR-capable), prores
  codec_policy: prefer_hardware   # prefer_hardware (default) or strict
  hdr_mode: auto                  # auto, sdr, hdr
  quality: "balanced"            # high, balanced, fast (shorthand for CRF presets)
  crf: null                      # unset = derived from quality; 0-51 overrides (lower = better)
```

CRF is the image-quality authority. `quality` is only a shorthand used when `crf` is omitted; an
explicit `crf` wins. The number is on **libx265's CRF scale**, and every other encoder is
calibrated against it: each backend gets whatever setting reproduces the same picture, measured by
SSIM, rather than the same integer. Lower CRF means higher quality everywhere. The measured table
per encoder is on
[the hardware overview](../deploy/hardware.md#quality-one-dial-calibrated-per-encoder).

The presets are points on that curve, measured on 1080p60 film:

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | ~35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | ~12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | ~12 MB, encoded as fast as the backend can |

There is no tier below `balanced`: around SSIM 0.980 gradients start to band. `fast` keeps the
balanced picture and buys its speed from the encoder effort preset instead, overriding
`hardware.encoder_preset`. `medium` and `low` are retired names that still load, resolving to
`balanced` and `fast`.

`codec_policy` decides what happens when the machine has no hardware encoder for the codec you
asked for but does have one for the other. `prefer_hardware` (the default) switches codec and says
so in the log and the run record, which on a chip like Intel Gemini Lake (H.264 encode entrypoint,
no HEVC one) is the difference between a film finishing and the CPU doing all of it. `strict`
always honours `output.codec` and accepts the CPU cost. The switch never applies to ProRes, and
never to an HDR output.

Containers and codecs pair up: `mp4` and `mov` with `h264`, `h265` or `prores`, and ProRes requires
MOV. `generate --format` accepts only `mp4`, `h265`, and `prores`: they select H.264/MP4,
H.265/MP4, and ProRes/MOV respectively. Internal and UI overrides also represent `h264_mov` and
`h265_mov`, but `h264_mov` and `h265_mov` are not CLI choices.

`hdr_mode: auto` preserves detected HLG or PQ sources when `codec: h265` is selected, converting
SDR clips, photos and title screens into the chosen HDR transfer before blending. H.264 is always
SDR: with `codec: h264`, `auto` tone-maps detected HDR sources and logs the reason. Use
`hdr_mode: sdr` when SDR is intentional, or `hdr_mode: hdr` with H.265 to force an HDR output from
SDR sources.

## Photos

```yaml
photos:
  enabled: true                  # Include photos in memories
  duration: 4.0                  # Seconds per photo clip (1-10)
  burst_window_seconds: 300      # Near-identical photos this close apart are one burst (0-3600)
  burst_hash_threshold: 8        # Hash bits two photos may differ by and still be one burst (0-64)
```

The animation per photo (Ken Burns, face pan, blurred background) is picked from the photo's
content and is not configurable.

Burst de-duplication keeps only the best-scored frame of a run of near-identical photos, so fifteen
shots of the same jump do not become fifteen clips. `burst_window_seconds: 0` all but turns it off:
photos sharing an identical timestamp still group.

## Hardware acceleration

```yaml
hardware:
  enabled: true                  # false = CPU encoding, no GPU probing at all
  backend: "auto"                # auto, none, nvidia, apple, vaapi, qsv
  encoder_preset: "balanced"     # fast, balanced, quality
  gpu_decode: true               # Hardware video decoding
```

`auto` detects the backend (NVIDIA NVENC → Apple VideoToolbox → Intel QSV → VAAPI, first hit wins).
`hardware.enabled: false` is the only way to force CPU. On multi-GPU Linux hosts pick the card with
`CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES`.

Naming a backend probes that one and nothing else, which is for measuring rather than for running.
A named backend that cannot encode here logs a warning and falls back to software. `backend` covers
the video render; the burst merge during download still detects for itself.

`encoder_preset` controls encoder speed and effort; it does not replace `output.crf`. On Apple,
`fast` enables VideoToolbox's speed-priority mode while `balanced` and `quality` leave it disabled.

## Audio and music

Background music needs `ace_step.enabled` or `musicgen.enabled`. With both on, ACE-Step generates
and MusicGen is the fallback generator and the stem separator used for ducking; with MusicGen off,
stems come from a local Demucs install if there is one. Per run, `--music PATH` uses your own file
and `--no-music` skips music. Music volume is per run too (`--music-volume`); the ducking and the
2 s / 3 s fades are fixed.

```yaml
musicgen:
  enabled: false                 # Use a MusicGen API server
  base_url: "http://localhost:8000"
  api_key: ""
  timeout_seconds: 10800         # 3 hours (60-18000)
  num_versions: 3                # Versions generated for selection (1-5)
  hemisphere: "north"            # north or south, for seasonal prompts

ace_step:
  enabled: false                 # Use ACE-Step (remote server or local library)
  mode: "api"                    # api (remote REST server) or lib (local, requires Python 3.12)
  api_url: "http://localhost:8000"
  api_key: ""                    # Bearer token for a protected ACE-Step server (api mode)
  model_variant: "turbo"         # Default 2B; use acestep-v15-xl-turbo for the 4B production profile
  lm_model_size: "1.7B"          # Default planner; use 4B with the XL production profile
  use_lm: false
  num_versions: 3                # 1-5
  hemisphere: "north"
  timeout_seconds: 3600          # 60-18000

audio:
  local_music_dir: "~/Music/Memories"   # Library scanned by `immich-memories music search`
  max_regenerations: 2                  # Extra auto-mode takes when the first is flagged (0-3)
  music_block_seconds: 120              # Longest single take before auto mode chains distinct takes (30-300)
  max_music_blocks: 3                   # Distinct takes to chain for a longer video (1-6)
```

`audio.local_music_dir` only feeds the `immich-memories music` helper commands; generation never
picks music from it on its own: pass the file with `--music`.

`audio.max_regenerations` bounds auto mode's reaction to a generated track the cheap quality gate
flags as a repetitive "tic-tac". The first take is scored; if it is flagged, auto mode generates
up to that many more takes and keeps the best-scored one. It never drops music, so a run ends with
a track even when every take is flagged.

A video longer than `audio.music_block_seconds` is not one long generation. Auto mode generates up
to `audio.max_music_blocks` distinct same-caption takes and joins them with crossfades, then loops
the sequence to fill the remaining length. One long take reads as a metronomic ramble, and one
short phrase on repeat is its own kind of monotony; a chain of a few distinct takes is neither.

## LLM (vision model)

Used by the reader and by title generation. Any OpenAI-compatible or Anthropic-compatible endpoint
works: mlx-vlm, oMLX, Ollama, vLLM, Groq, OpenAI, Claude, z.ai.

```yaml
llm:
  provider: "openai-compatible"   # openai-compatible | openai | zai | anthropic | ollama
  base_url: "http://localhost:8080/v1"
  model: ""                        # e.g. mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
  api_key: ""                      # optional, only for cloud APIs
  timeout_seconds: 300             # increase for slow local models (10-3600)
  send_image_detail: true          # off: APIs whose strict schema rejects image_url.detail
  always_reasons: false            # true: the endpoint thinks on every call, asked or not
  thinking: "disabled"             # disabled | low | high | max | auto
  reader_concurrency:              # independent reader jobs; unset reads it from base_url
  batch: "off"                     # off | auto: queue a stage's independent prompts, half price
  batch_min_requests: 8            # fewest independent prompts in a stage worth queueing
  batch_max_wait_minutes: 60       # then ask whatever the batch has not answered in real time
  # thinking_params:               # what the switch looks like on your server
  #   chat_template_kwargs:        # (default: the Qwen dialect, vLLM/mlx)
  #     enable_thinking: true
  # no_thinking_params:            # how to say "don't reason" to that server
  #   chat_template_kwargs:        # (default: the Qwen dialect, vLLM/mlx)
  #     enable_thinking: false
```

**The goal of this product is a fully local process**: your photos analyzed on your own hardware,
nothing leaving your network. A local server (mlx, vLLM, Ollama) is the intended setup. The cloud
providers exist so that people without the means to run a local model can still use the product.
Using one sends each analyzed clip's frames or thumbnails and the derived descriptions to that
provider.

`openai`, `anthropic` and `zai` are presets: the right adapter with the vendor's URL and reasoning
dialect filled in. An explicit `base_url` always wins, and under `zai` it also picks the adapter (a
`.../api/anthropic` base takes the Messages route). Which dialect goes where, what `thinking` does
on each host, how `thinking_params` and `no_thinking_params` differ, and what batching pays are all
on [The reader](../deploy/readers.md), with the measured comparison of ten models.

`thinking` has five settings. `disabled` never asks for reasoning. `low`, `high` and `max` run the
model in reasoning mode for two calls: title generation, and the special-day question in
`discover-days`. `auto` sends no reasoning field and takes the host's default, which is where to
start on a host whose dialect you do not know. `true` and `false` still parse, as `high` and
`disabled`. Reasoning is refused alongside images whatever this is set to: reasoning over several
pictures is a measured runaway. Measured on the live endpoint, a thinking call ran 30-134 s where
the same model answered in 4-7 s without it, and needs a 4000-token ceiling to finish.

The `openai` preset sends `reasoning_effort: none` for `gpt-5.6-luna` and its dated snapshots
on non-thinking calls. Older GPT-5 models keep `minimal`. An explicit setting wins over the preset.
A provider that rejects a reasoning value reports that error; it does not silently remove the
control and fall back to default reasoning. Only rejection of the parameter itself permits that
fallback.

`always_reasons` covers a reasoning model that bills its private thinking inside `max_tokens`, so
the budget the reader asked for its answer is the budget the thinking spends first. Measured on one
hosted API with the same 17 KB monthly read: 245 thinking tokens on the lightest model, 4,126 and
6,256 on two others and 13,469 on the heaviest, all returning HTTP 200 and an empty answer at the
reader's 4,000-token ask. Such calls now ask for the cheapest reasoning the host sells and add
16,384 tokens of room on top of the caller's cap, so the cap keeps meaning what it says about the
answer. The room is a ceiling, not a bill. It is learned from the first reply that reports reasoning
tokens and remembered per server and model; set `always_reasons: true` to spare that first call,
which otherwise comes back empty.

`reader_concurrency` limits independent reader jobs in flight (1 to 16). Independent episode-evidence
packs, event inventories and worthiness/standing blocks can overlap. Pages within an event,
story-episode pages and later dependent picks remain sequential. Scheduling preserves prompt text,
judgment keys and source ordering; batch delivery is configured separately.

Left unset, concurrency is read from `base_url`: 1 for a loopback, private address or bare service
name, 4 for a public host. See
[Reader concurrency](../deploy/configuration/config-file.md#reader-concurrency). A provider that
answers 429 pauses every reader in the run, each waiting a slightly different span.

`send_image_detail` sends OpenAI's optional `image_url.detail` field. Set it to `false` for strict
vision schemas that reject anything beyond `image_url.url`; the `zai` preset already does.

A provider's dialect can be declared up front instead of negotiated:

```yaml
llm:
  max_tokens_param: max_completion_tokens  # example; the default is max_tokens
  drop_params: [temperature]               # example; the default is []
  extra_params: {}                         # fields merged into every call
```

`max_tokens_param` and `drop_params` are read only on the OpenAI dialect, where the query layer
otherwise learns them from the provider's 400s and remembers the answer per server and model.
`extra_params` also applies on Ollama, where anything under `options` (`num_ctx`, `num_predict`) is
merged into Ollama's own options block rather than replacing it, and a `num_predict` you set there
wins over the reasoning room the run would otherwise compute.

A separate `title_llm` section can point title generation at a different model, for the CLI and the
web UI alike:

```yaml
title_llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "llama3.2"              # example; the default is empty, which means "use llm"
  api_key: ""
  timeout_seconds: 300
  send_image_detail: true        # same switch as llm.send_image_detail
  always_reasons: false          # same switch as llm.always_reasons
```

The switch is all-or-nothing on `title_llm.model`: when it is set the whole `title_llm` block is
used, and any field you leave out takes the *built-in* default, not the one from `llm`. When
`title_llm.model` is empty, `llm` is used.

## Triage heads

```yaml
triage:
  enabled: false                 # Legacy standalone triage hook; editorial preparation runs independently
  encoder: ~/.immich-memories/models/triage/dinov2-small.onnx  # DINOv2-small ONNX export (88 MB)
  encoder_url: https://github.com/...    # where `models fetch` downloads that export from
  bundle: ""                     # Head weights (.npz); empty = the public bundle in the package
  provider: auto                 # ONNX Runtime provider for the encoder: auto, cpu, cuda, coreml
```

`provider: auto` takes CUDA where that provider is present and CPU everywhere else. It never takes
CoreML: measured on the pinned export, CoreML claims 274 of the 513 nodes and splits the graph into
87 partitions, so it runs 6 to 8 times slower than the CPU provider and holds 9 times the resident
memory. Set `provider: coreml` to re-measure it. The choice is operational: it does not enter the
encoder key, so changing it re-derives nothing.

Editorial preparation uses `triage.encoder` with the public six-head bundle from
`editorial.preparation.head_bundle`, and checks its digest on load. Missing required head facts
stop selection; `triage.enabled: false` does not bypass preparation.

Only `encoder` and `encoder_url` are read. `enabled` and `bundle` are left over from the standalone
triage hook: they load, they validate, they do nothing.

## Editorial planner

```yaml
editorial:
  reader: auto                  # auto | model | rules
  annotation_database: ""        # defaults to annotations.sqlite inside the configured cache directory
  description_model: "smolvlm2-500m-base-public@envelope-v3-compact"
  pixel_producer_key: "pixel-facts-v1"  # exact producer of pixel facts and thresholds  # gitleaks:allow
  head_versions:                 # exact producer version selected for each annotation head
    activity: public-v1
    children: public-v1
    doc_docling: det-v2
    location: public-v1
    nsfw_marqo: det-v2
    people: public-v1
    swim: oi-v3
    venue: oi-v3
  preparation:
    tier: full                   # full | no_captions | metadata_only
    caption_base_url: http://localhost:8092/v1
    caption_artifact_id: ""   # optional artifact/revision label; existing captions stay banked
    caption_api_key: ""          # bearer token for a caption server that requires one
    caption_timeout_seconds: 90
    caption_concurrency: 1                # raise it for a captioner on a GPU
    batch_size: 32
    head_bundle: ""              # packaged public six-head bundle
    detector_python: ""          # current Python interpreter
    detector_cache_dir: ""       # normal Hugging Face Hub cache
    marqo_onnx: ~/.immich-memories/models/detectors/nsfw-marqo-384.onnx  # digest-pinned sensitive-content export
    marqo_onnx_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/nsfw-marqo-384-924658f1.onnx
    allow_model_downloads: false
```

Tier 2: lives under `advanced:` when the app writes the file.

Docling uses `det-v2`, because ONNX layout optimization mislabels documents on the Celeron J4125.
Saved `doc_docling: det-v1` settings upgrade on load, and the next run recomputes that head's facts
and refreshes dependent readings; other head facts stay reusable.

`reader: auto` uses the model when `llm.model` is set and rules when it is blank. `reader: model`
requires a model; `reader: rules` skips model editing and reranking even when a model is
configured. Rules cover the ten standard memory products, including albums and recurring dates,
from dates, places, favourites, people metadata and whatever preparation facts exist. They reuse
the normal allocation, spacing, audience and timing checks, omit a thesis, keep unsampled Live
Photos as stills, and write no semantic model banks. Saved plans identify the producer as
`rules-v1`. A custom free-text subject ("pictures about perseverance") needs the model reader.

Preparation is a separate choice: `rules` plus `no_captions` keeps the image classifiers, `rules`
plus `metadata_only` produces only previews and pixel measurements. For a no-inference comparison,
use `metadata_only` with a fresh annotation database; changing the tier does not erase model facts
already stored.

### Preparation tiers

`preparation.tier` names which producers a deployment asks for. It is a named choice, never a
fallback: a producer the tier demands and cannot reach still stops the run.

| `tier` | What runs | First pass over about ten thousand pictures on a Celeron J4125 NAS |
| --- | --- | --- |
| `full` | pixels, encoder + six heads, both detectors, captions | 4 days |
| `no_captions` | pixels, encoder + six heads, both detectors | 3 h 41 min |
| `metadata_only` | pixels and Immich metadata; no ONNX, no captions | minutes |

`no_captions` is the tier for a low-power NAS. The captioner costs 25 times the rest of the
pipeline put together, and dropping it keeps every producer the audience gate reads
(`nsfw_marqo`, `swim`, `children`, `exposure`, `doc_docling`), so the gate is unchanged.
Reasons under each picture become facts rather than sentences.

`metadata_only` also drops the six heads and both detectors, so the gate loses its evidence.
It therefore holds **every** unit to `family_only` and refuses a `sendable` export outright.
Use it only on a machine that cannot run ONNX at all.

Captions are banked per picture, so a `no_captions` deployment can add them later and switch
the tier to `full` when it finishes.

Every run uses the **FAMILY** audience. Ordinary family material, including a shirtless baby, bath
time, breastfeeding or a parent holding a newborn in hospital, can be considered; graphic medical
procedures, sexual content, exposed adult changing and identifying records are excluded.

Preparation fills missing descriptions, public heads, detectors and pixel measurements in the
annotation database, and skips provider calls where the facts are complete. Missing previews or
providers stop selection with an explicit incomplete result. See
[Editorial annotation setup](../deploy/configuration/editorial-preparation.md) for the runtime
extra, the exact model artifacts and the caption endpoint requirements.

## Inference service

```yaml
advanced:
  inference:
    facts_base_url: ""          # blank: the heads and detectors run in the app process
    timeout_seconds: 60         # one picture, one request; the service answers all producers at once
    facts_concurrency: 8        # how many of those requests are in flight at once (1-32)
    producers: [heads, nsfw_marqo, doc_docling]   # what the service answers for; the rest stay local
    fallback_to_local: true     # when the service cannot be reached, run the in-process producers
```

Point `facts_base_url` at a running [inference service](../deploy/installation/inference-service.md)
(`http://inference:8092` in the compose profile) and `prepare` and `generate` send each picture's
preview there once and bank what comes back. The row is the same row the in-process producers
write: same head, version, label and encoder key, because the service runs the application's own
producers and the key is computed over the model artifact, never over where it ran. Change the
provider or the host and nothing is re-derived.

`producers` narrows what is offloaded. `[heads]` sends the DINOv2 encoder and the six context heads
and keeps the two cheaper detectors in the app.

`facts_concurrency` is how many pictures are in the air at once. One at a time, measured on a
cluster against a T1000, costs 0.69 s a picture whatever the card is doing, because almost all of
it is the round trip: 3,709 pictures took 42.7 minutes, and a 13,552-picture month would have taken
2.6 hours. Answers are banked in the order the pictures were asked for, so raising this re-derives
nothing. Raise it until the service is the slow half; the ceiling is 32, and the service's own
`REQUEST_THREADS` decides how many it can answer at once.

When the service does not answer, the failure is recorded against the endpoint in the preparation
report, and with `fallback_to_local: true` the in-process producers take over for the pictures
still missing facts (which needs the model files from `models fetch` on the app box). With it off,
the cut refuses until the service is back.

## Title screens

```yaml
title_screens:
  enabled: true                  # Opening title, month dividers and ending screen
  title_duration: 3.5            # seconds (1-10)
  month_divider_duration: 2.0    # seconds (1-5)
  ending_duration: 7.0           # seconds (2-15)
  locale: "auto"                 # en, fr, or auto-detect
  style_mode: "auto"             # auto (mood-based) or random
  animated_background: true      # Gradient shift and colour pulse behind the text
  show_decorative_lines: false   # Line accents around the title text
  show_month_dividers: true      # When the video spans several months (all-or-none)
  month_divider_threshold: 2     # Min clips in a month to show its divider (1-10)
  use_first_name_only: true      # "Riley" instead of "Riley Smith" in titles
```

`animated_background` and `show_decorative_lines` are all the look-and-feel the config file
exposes; the colour palette and custom fonts are not configurable today.
`animated_background: false` keeps the gradient still
 (no rotation, colour pulse or vignette pulse), which is what `preset: fast` selects. The
`immich-memories titles` command exposes more of the look as flags for previewing.

## Trip detection

```yaml
trips:
  homebase_latitude: 0.0
  homebase_longitude: 0.0
  min_distance_km: 50
  min_duration_days: 2
  max_gap_days: 2
```

## Cache

```yaml
cache:
  directory: "~/.immich-memories/cache"
  database: "~/.immich-memories/cache.db"
  max_age_days: 30               # Analysis cache expiry (1-365)
  video_cache_enabled: true      # Cache downloaded videos locally
  video_cache_max_size_gb: 10.0  # Max disk usage for video cache (1-500 GB)
  video_cache_max_age_days: 7    # Auto-delete cached videos older than this (1-365)
  thumbnail_cache_max_size_mb: 10000.0 # Max disk for Immich previews (50 MB-100 GB)
  preview_cache_max_size_mb: 2000.0    # Max disk for clip previews (100 MB-100 GB)
```

Tight on disk: lower `video_cache_max_size_gb`, or turn it off with `video_cache_enabled: false`.

### Size the thumbnail cache by your library

`thumbnail_cache_max_size_mb` is the one cache budget that scales with the library. Every candidate asset in a memory's scope gets an Immich preview fetched and read back several times. Measured on a real library, one preview is about **315 KB**, so:

```
budget in MB ≈ 0.35 × (assets a memory's scope can reach)
```

A scope of ten thousand candidates wants about 3.4 GB; the `0.35` leaves a little headroom over the measured 0.315 MB. The 10 GB default holds roughly 31,000 previews, which covers three scopes that size.

If the run's working set does not fit, nothing is lost mid-run: previews still in use are never deleted and the cache overflows the limit instead. The *next* run reclaims them, so the next overlapping memory re-downloads every preview. You get one `WARNING` per run saying how far over you are. Raise it rather than ignoring it.

The other two budgets are not library-sized: `preview_cache_max_size_mb` holds the video renditions the wizard's player streams, and the video cache holds the originals being assembled. Both are tens of files per run, however big your library is.

## Server (UI)

```yaml
server:
  host: "0.0.0.0"               # Listen address. Without auth and without this set
                                 # explicitly, the UI binds 127.0.0.1 (secure default)
  port: 8080                     # Listen port (1-65535)
  enable_demo_mode: false        # Show the demo/privacy (blur) toggle in the sidebar
  secure_cookies: false          # Mark the session cookie Secure (turn on behind an HTTPS reverse proxy)
  trigger_token: ""              # Shared secret for POST /api/trigger. Empty, and with auth
                                 # off, the trigger API is not served at all
  allow_unauthenticated_lan: false  # Listen beyond localhost with auth disabled:
                                 # anyone reaching the port can use the UI and the
                                 # Immich library behind it
```

`host` and `port` also have CLI flags: `immich-memories ui --host 127.0.0.1 --port 9090`. The rest
of the section is config-only.

`trigger_token` turns on the HTTP trigger: one POST that runs whatever `auto run` would have
decided, so an Immich workflow (or a cron, or a phone shortcut) can start a memory. See
[Trigger from Immich or anything else](../create/recipes/automated-generation.md#trigger-it-over-http).
Keep it out of `config.yaml` with `IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN`: `server` does not expand
a `${VAR}` reference, so writing one here stores the six literal characters as your token. Either
way the value is redacted from `/health`, the config viewer and the logs, from config load onwards.

`host` is the one value "save" leaves out of `config.yaml` when you never set it: writing the
`0.0.0.0` default would make the next load treat it as your decision and retire the localhost bind.
A `server.host: 0.0.0.0` already in your file is ignored with a warning and disappears the next
time the file is saved. Any other address is kept, and so are `--host` and
`IMMICH_MEMORIES_SERVER__HOST`. To keep a LAN bind with authentication off, use
`allow_unauthenticated_lan: true`.

## Upload to Immich

```yaml
upload:
  enabled: false
  album_name: null               # Created if missing, reused if exists
```

## Scheduler

```yaml
scheduler:
  enabled: false
  timezone: "UTC"
  job_timeout_minutes: 120  # Whole-job deadline, including preparation and rendering; must be positive
  schedules:
    - name: "yearly-recap"
      memory_type: "year_in_review"
      cron: "0 9 15 1 *"
      enabled: true
      upload_to_immich: false
      album_name: "{year} Memories"
      person_names: []
      duration_minutes: null
      params: {}
```

## Automation

Controls what `immich-memories auto suggest` and `auto run` detect and generate. See the [auto CLI docs](../create/cli/auto.md) for the full command reference. Tier 2: lives under `advanced:` when the app writes the file.

```yaml
automation:
  enabled: false                  # run the daily auto-run decision inside the web UI process (Docker)
  daily_at: "09:00"               # HH:MM, local time of that process (container TZ)
  cooldown_hours: 24              # min hours between auto-generated memories (1-168)
  max_delivery_attempts: 5        # give up on an Immich upload after this many failures (1-50)
  upload_to_immich: false         # auto-upload results
  album_name: null                # target album for uploads
  detect_monthly: true            # monthly highlights candidates
  detect_yearly: true             # year-in-review candidates
  detect_trips: true              # GPS trip detection (needs homebase coords)
  detect_person_spotlight: true   # per-person highlight candidates
  detect_activity_burst: true     # unusually active months
  burst_threshold: 2.0            # multiplier above rolling average to trigger burst
```

## Authentication

Protects the web UI. See the [Authentication guide](../deploy/configuration/authentication.mdx) for provider-specific setup (OIDC examples, header proxy config, etc.).

```yaml
auth:
  enabled: false
  provider: basic                # basic, oidc, or header
  session_ttl_hours: 24          # 1-720
  public_url: ""                 # e.g. https://memories.example.com -- the URL users reach you
                                 # on. Pins the OIDC redirect_uri and enables callback-origin
                                 # validation; without it no origin check is performed

  # Basic auth
  username: ""
  password: ""                   # Supports ${ENV_VAR} expansion

  # OIDC / SSO
  issuer_url: ""                 # Auto-discovers via /.well-known/openid-configuration; supports ${ENV_VAR}
  client_id: ""                  # Supports ${ENV_VAR} expansion
  client_secret: ""              # Supports ${ENV_VAR} expansion; empty for public clients
  scope: "openid email profile"
  allowed_emails: []             # OIDC: addresses that may sign in. Empty = anyone the IdP
  allowed_domains: []            # authenticates. Domains match exactly: example.com does
                                 # not admit sub.example.com
  auto_launch: false             # Skip login page, redirect straight to IdP
  button_text: "Sign in with SSO"

  # Trusted header (reverse proxy)
  user_header: "Remote-User"
  email_header: "Remote-Email"
  trusted_proxies: []            # IPs/CIDRs of your proxy. Required for header provider;
                                 # for basic/oidc their X-Forwarded-* headers are trusted
```

Place under `advanced:` in your config file (like all Tier 2 sections).

## Notifications

Get notified when auto-generation or scheduled jobs complete. Uses [Apprise](https://github.com/caronc/apprise) (130+ services: ntfy, Discord, Telegram, Slack, email, webhooks). Apprise ships with the base package, no extra to install. Tier 2: lives under `advanced:` when the app writes the file.

```yaml
notifications:
  enabled: false
  urls:                           # Apprise notification URLs
    - "ntfy://ntfy.sh/my-topic"
    - "discord:///webhook_id/token"
    - "tgram://bot_token/chat_id"
  on_success: true                # notify on successful generation
  on_failure: true                # notify on failed generation
  attach_thumbnail: false         # opt in; attachments cost bandwidth/provider quota
  cooldown_hours: 24              # pause normal attempts after a delivery failure (1-168)
```

Delivery failures are stored as sanitized health state. Normal success and failure
notifications pause during the cooldown instead of hammering a quota-limited provider.
`auto test-notification` always bypasses the cooldown and a successful test clears it.
Provider URLs, credentials, and response bodies are never included in health output.

Test your config: `immich-memories auto test-notification`
