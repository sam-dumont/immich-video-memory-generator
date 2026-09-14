---
title: Config Reference
sidebar_label: Config Reference
---

# Config Reference

Use `~/.immich-memories/config.yaml` to override these settings. Examples show built-in defaults unless a comment marks an example. Start with the [configuration guide](../deploy/configuration/config-file.md) if you only need the Immich connection.

:::tip Config tiers
Tier 2 sections (`analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`,
`automation`, `notifications`, `triage`, `editorial`, `inference`) are
written under an `advanced:` key when the app saves the file:

```yaml
advanced:
  analysis:
    max_album_assets: 5000
  hardware:
    encoder_preset: "quality"
```

Both placements work when reading. If a setting appears in both, its top-level value wins;
other settings from the `advanced:` section are kept. The remaining sections, including
`title_llm`, stay at the top level.

Removed settings produce a warning and are dropped; saving the config removes them. Unknown
keys inside a section are otherwise ignored. Unknown top-level keys and invalid values fail
validation. Check the startup warnings after an upgrade.
:::

## Preset

One top-level switch that fills several knobs at once. `fast` reduces render cost; it does not disable GPU encoding or change image preparation.
Anything you set yourself (a key in the file, an `IMMICH_MEMORIES_…` env var, a CLI flag, a
choice in the web UI) wins over the preset.

```yaml
preset: null                       # null | fast
```

`fast` sets, unless you set them yourself: `output.resolution: 1080p`, `output.codec: h264`,
`output.quality: fast`, `hardware.encoder_preset: fast` and
`title_screens.animated_background: false` (static title backgrounds). Music generation is already
off by default and stays wherever you put it.

Env: `IMMICH_MEMORIES_PRESET=fast`. One-off on the CLI: `immich-memories --preset fast generate …`
(root option, before the subcommand). The settings page names the active preset; the options page defaults
its resolution to the preset's and says so.

Caveat: **the Memory page's "Save Config"** (under Advanced) writes every value to `config.yaml`, not just the connection
fields it appears to be about, after which they all count as "set by you", and the preset has
nothing left to fill in. Remove the keys you want the preset to own again. (`server.host` is the
single exception; see [Server (UI)](#server-ui).) The `/settings/config` page is read-only and
does not save; see [Settings](../create/web-ui/settings.mdx).

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

The compatibility boundary normalizes v2 duration strings and v3 millisecond durations to
seconds, chooses the matching upload fields before any file upload, and emits timezone-aware
search dates accepted by v3. Run the read-only `immich-memories config test` command to check
credentials and see the resolved API contract without generating or uploading a memory.

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
  exclude_stills_without_camera_exif: true   # Exclude photos without camera metadata
  min_source_short_side: 1080    # Drop smaller clips unless they carry camera EXIF

  # Album source
  max_album_assets: 10000        # Most assets read from one album, per media type (min 1)

  # Downloads
  download_workers: 3            # Parallel download clients for video and thumbnail prefetching (1-8)

  # Duration sizing
  optimal_clip_duration: 5.0     # Expected seconds per clip when a trip or album sizes its own duration (2-15s)

  # Live Photos (iPhone 3s video clips)
  include_live_photos: true      # Include Live Photo clips (ON by default)
  live_photo_merge_window_seconds: 10.0  # Max gap to group as burst (1-60s)
  live_photo_min_clip_seconds: 3.5       # Minimum usable burst duration; motion is checked too (0-30s)
```

Any Live Photo cluster of two or more within the merge window is treated as a burst; the count
is not configurable. Where a clip is cut, and how long it runs, is the editor's decision per
carrier; there is no pacing preset any more.

`max_album_assets` applies per media type, so the default reads up to 10,000 videos and 10,000
photos from one album. Smart albums reach tens of thousands; Immich returns newest first, so a
bigger album may be truncated and you get a warning naming it. Narrow the
album, raise the cap, or use a date range instead.

## Generation defaults

```yaml
defaults:
  scale_mode: "blur"             # blur | fit (black bars); used when --scale-mode is not given
  transition: "smart"            # cut, crossfade, smart, none (used when --transition is left on smart)
  transition_duration: 0.5       # 0-2 seconds
```

Target duration and orientation are chosen per run: the UI slider / `--duration` (seconds) and
`--orientation`, with the memory type preset supplying the default duration; there is no config
default for either. The target duration describes the finished video, not just the selected source
clips. The planner budgets opening/title/ending cards, then adds back the time the fades overlap
away, so the content budget ends up larger than the timeline left over, not smaller. Divider capacity is budgeted before rendering. The renderer inserts the first eligible
changes up to that limit; it does not add a divider for the opening month. There is no backfill: if the stories the editor funded do not fill the budget, the run
reports the shortfall instead of padding it with material it had already decided against.
Treat the target as a target: frame and transition boundaries mean the encoded file lands near the
requested duration, not exactly on it.

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

CRF is the image-quality authority. `quality` is only a shorthand used when `crf` is omitted;
an explicit `crf` wins. The number is on **libx265's CRF scale**, which is the reference every
other encoder is calibrated against: each backend gets whatever setting reproduces the same
picture, measured by SSIM, rather than the same integer. See
[the hardware overview](../deploy/hardware/overview.md#quality-one-dial-calibrated-per-encoder)
for the measured table. Lower CRF still means higher quality everywhere.

| `quality` | Reference CRF | Encoder effort |
| --- | --- | --- |
| `high` | 18 | Configured `hardware.encoder_preset` |
| `balanced` | 24 | Configured `hardware.encoder_preset` |
| `fast` | 24 | Fast preset |

`fast` keeps the balanced quality target and reduces encoder effort. Actual file size depends
on the source and encoder; CRF is not a bitrate or file-size limit.

`codec_policy` decides what happens when the machine has no hardware encoder for the codec you
asked for but does have one for the other. `prefer_hardware` (the default) switches codec and says
so in the log and the run record, which on a chip like Intel Gemini Lake (H.264 encode entrypoint,
no HEVC one) is the difference between a film finishing and the CPU doing all of it. The file is
bigger and plays on more things. `strict` always honours `output.codec` and accepts the CPU cost.
The switch never applies to ProRes, and never to an HDR output, because H.264 carries no HDR.

The final encoding plan permits only `mp4` and `mov` containers with `h264`, `h265`, or `prores`
codecs. `generate --format` accepts only `mp4`, `h265`, and `prores`: they select H.264/MP4,
H.265/MP4, and ProRes/MOV respectively. Config can select compatible codec/container pairs;
internal and UI overrides also represent `h264_mov` and `h265_mov`, but `h264_mov` and `h265_mov`
are not CLI choices. ProRes requires MOV; H.264 and ProRes do not support HDR output.

`hdr_mode: auto` preserves detected HLG or PQ sources when `codec: h265` is selected. It converts
SDR clips, photos, and title screens into the chosen HDR transfer before blending, so intermediate
files do not all need to carry HDR metadata. H.264 is always SDR: with `codec: h264`, `auto`
tone-maps detected HDR sources and logs the reason. Use `hdr_mode: sdr` when SDR is intentional, or
`hdr_mode: hdr` with H.265 to force an HDR output even when every source is SDR.

## Photos

```yaml
photos:
  enabled: true                  # Include photos in memories
  duration: 4.0                  # Seconds per photo clip (1-10)
  burst_window_seconds: 300      # Near-identical photos this close apart are one burst (0-3600)
  burst_hash_threshold: 8        # Hash bits two photos may differ by and still be one burst (0-64)
```

The animation per photo (Ken Burns, face pan, blurred background) is picked automatically from the
photo's content; it is not configurable.

Burst grouping uses timestamps and perceptual hashes to keep near-identical photos from
occupying separate moments. The editor chooses which representation to use. `burst_window_seconds: 0` all but turns
it off: photos sharing an identical timestamp still group.

## Hardware acceleration

```yaml
hardware:
  enabled: true                  # false = software video encoding; models and titles are separate
  encoder_preset: "balanced"     # fast, balanced, quality
  gpu_decode: true               # Hardware video decoding
```

The backend is detected automatically (NVIDIA NVENC → Apple VideoToolbox → Intel QSV → VAAPI, first
hit wins); there is no override. `hardware.enabled: false` is the only way to force CPU. On multi-GPU
Linux hosts pick the card with `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES`.

`encoder_preset` controls encoder speed/effort; it does not replace `output.crf`. On Apple,
`fast` enables VideoToolbox's speed-priority mode while `balanced` and `quality` leave it disabled.
Image quality still comes from the CRF translation described above.

## Audio and music

Music is optional. `--music PATH` uses your file; `--no-music` skips it. Otherwise an enabled
ACE-Step or MusicGen backend can generate a track, with bundled music as a fallback when
installed. With no available track, the run continues without background music. The UI also
lets you choose a file, generated music, bundled music, or none.

`--music-volume` and the UI slider set the music level. The mixer ducks music under source
audio and uses fixed 2-second fade-in and 3-second fade-out durations.

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
```

`audio.local_music_dir` only feeds the `immich-memories music` helper commands; generation never
picks music from it on its own: pass the file with `--music`.

## LLM (vision model)

Used by the model reader, title generation and music mood analysis. The model must support images for picture-based requests. Caption preparation has its own endpoint under `editorial.preparation`. See [LLM setup](../create/pipeline/llm-content-analysis.md) for server configuration.

```yaml
llm:
  provider: "openai-compatible"   # openai-compatible | openai | zai | anthropic | ollama
  base_url: "http://localhost:8080/v1"
  model: ""                        # e.g. mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
  api_key: ""                      # required if the endpoint uses bearer authentication
  timeout_seconds: 300             # increase for slow local models (10-3600)
  send_image_detail: true          # off: APIs whose strict schema rejects image_url.detail
  thinking: false                  # server has a reasoning switch
  # thinking_params:               # what the switch looks like on your server
  #   chat_template_kwargs:        # (default: the Qwen dialect, vLLM/mlx)
  #     enable_thinking: true
  # no_thinking_params:            # how to say "don't reason" to that server
  #   chat_template_kwargs:        # (default: the Qwen dialect, vLLM/mlx)
  #     enable_thinking: false
```

A remote endpoint receives the pictures and text sent to it, including names and places in
annotations. A local endpoint keeps those model requests on your own hardware. See
[Network & Privacy](../deploy/configuration/network-and-privacy.md) for other outbound requests.

`openai-compatible` uses chat completions; `anthropic` uses the native Messages API. `ollama`
uses Ollama's native API. `openai` and `zai` supply provider defaults, which explicit settings
can override. For `zai`, a base URL ending in `/api/anthropic` selects the Anthropic adapter.

`thinking` requests reasoning for title generation and the special-day check. OpenAI and
Anthropic image calls disable the app's thinking flag, but the server may still reason.
`thinking_params` and `no_thinking_params` describe the OpenAI-compatible request dialect;
the defaults use `chat_template_kwargs.enable_thinking`. The off parameters are sent on
non-thinking calls too. Use `{}` if the server needs no explicit off switch. The native
Ollama path does not use these two parameter blocks.

`send_image_detail: false` omits `image_url.detail` for endpoints that reject it. The request
layer adapts some rejected parameters and logs provider errors, but an OpenAI-compatible URL
does not guarantee that its model accepts every request.

A provider's dialect can also be declared up front instead of negotiated:

```yaml
llm:
  max_tokens_param: max_completion_tokens  # example; the default is max_tokens
  drop_params: [temperature]               # example; the default is []
  extra_params: {}                         # fields merged into every call
```

`max_tokens_param` and `drop_params` describe the OpenAI dialect and are read
only there. `extra_params` applies on the Ollama provider too, where anything
you put under `options` (`num_ctx`, `num_predict`) is merged into Ollama's own
options block rather than replacing it. The app does not set `num_ctx` automatically;
configure a context window that fits your chosen model and request.

A separate `title_llm` section can point title generation at a different model than the one used for
content analysis. It applies to the CLI and the web UI alike:

```yaml
title_llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "your-title-model"     # example; the default is empty, which means "use llm"
  api_key: ""
  timeout_seconds: 300
  send_image_detail: true        # same switch as llm.send_image_detail
```

The switch is all-or-nothing on `title_llm.model`: when it is set the whole `title_llm` block is
used, and any field you leave out takes the *built-in* default (`provider: openai-compatible`,
`base_url: http://localhost:8080/v1`, empty `api_key`); it is not inherited from `llm`. When
`title_llm.model` is empty, `llm` is used. Both entry points resolve it the same way.

## Triage heads

```yaml
triage:
  encoder: ~/.immich-memories/models/triage/dinov2-small.onnx  # DINOv2-small ONNX export (88 MB)
  encoder_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/dinov2-small-478164cd.onnx
  provider: auto                 # ONNX Runtime provider for the encoder: auto, cpu, cuda, coreml
```

`provider: auto` selects CUDA when available and CPU otherwise; CoreML must be selected
explicitly. This setting controls the DINOv2 encoder, not the document or sensitive-content
detectors. Changing device does not invalidate compatible saved facts.

Preparation uses `triage.encoder` and the heads configured by
`editorial.preparation.head_bundle`. Install the `editorial` extra and run `models fetch` to
install the pinned artifacts. Missing facts required by the selected tier stop the run.

The public heads provide context. They do not train on your library or independently decide
whether a picture is suitable for the audience.

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
    caption_api_key: ""          # bearer token for a caption server that requires one
    caption_timeout_seconds: 90
    caption_concurrency: 4
    batch_size: 32
    head_bundle: ""              # packaged public six-head bundle
    detector_python: ""          # current Python interpreter
    detector_cache_dir: ""       # normal Hugging Face Hub cache
    marqo_onnx: ~/.immich-memories/models/detectors/nsfw-marqo-384.onnx  # digest-pinned sensitive-content export
    marqo_onnx_url: https://github.com/sam-dumont/immich-video-memory-generator/releases/download/models-v1/nsfw-marqo-384-924658f1.onnx
    allow_model_downloads: false
```

Tier 2: lives under `advanced:` when the app writes the file. Story-first selection is the
production route for UI, CLI and scheduled runs. Old `enabled` and `story_first` keys are
ignored; there is no opt-in flag or environment switch.

Docling uses `det-v2` to avoid incorrect document labels from ONNX layout optimization on
the Celeron J4125. Saved `doc_docling: det-v1` settings upgrade on load. The next run
recomputes that head's facts and refreshes dependent readings; other head facts remain reusable.

`reader: auto` uses the model when `llm.model` is set and rules when it is blank.
`reader: model` requires a model; `reader: rules` skips model editing and reranking even
when a model is configured. Rules support the ten standard memory products, including
albums and recurring dates. Custom free-text subjects require the model reader: rules
cannot interpret a request such as "pictures about perseverance".

Rules use dates, places, favourites, people metadata and available preparation facts.
They reuse the normal allocation, spacing, audience and timing checks, omit a thesis,
keep unsampled Live Photos as stills, and do not write semantic model banks. Saved plans
identify the producer as `rules-v1`; the dedicated UI disclosure remains outside this slice.

Preparation remains a separate choice: `rules` plus `no_captions` retains image
classifiers; `rules` plus `metadata_only` produces only previews and pixel measurements.
For a **no-inference comparison**, use `metadata_only` with a fresh annotation database;
changing the tier does not erase model facts already stored there. The default `full`
tier still requires its caption producer, even with rules editing.

### Preparation tiers

`preparation.tier` names which producers a deployment asks for. It is a named choice, never a
fallback: a producer the tier demands and cannot reach still stops the run.

| `tier` | What runs |
| --- | --- |
| `full` | Pixel measurements, encoder and six heads, two detectors, captions |
| `no_captions` | The same image producers without captions |
| `metadata_only` | Pixel measurements and Immich metadata; no model producers |

`no_captions` keeps detector evidence and needs no caption server, but cannot clear the
caption-dependent findings. Both reduced tiers hold units at `family_only` and refuse a
`sendable` export. `metadata_only` also omits the heads and detectors; it supports the ten
standard memory types with simpler selection. Timings and hardware
requirements are in [Running modes](../deploy/running-modes.md).

Captions are banked per picture, so a `no_captions` deployment can add them later and switch
the tier to `full` when it finishes.

The planner reads the whole source period, identifies its stories and distinct moments,
then allocates duration and picks representations of those moments. A longer target can
show more of a story without inventing more events from near-duplicate pictures.

New runs use the **FAMILY** audience. Ordinary family scenes, such as a parent holding a
newborn in hospital, can be considered. The final gate excludes identified bathing,
breastfeeding, changing, graphic medical content, sexual content and identifying records.
Description-based findings need `full` preparation; reduced tiers cannot identify all of
these activities. Classification can be wrong: review the cut before sharing it.

Preparation fills missing descriptions, public heads, detectors and pixel measurements in
the annotation database. Complete facts skip provider calls. Missing previews or providers
stop selection with an explicit incomplete result. Two verified invalid caption completions
can be recorded as `caption unavailable`, counted separately from successful descriptions.
See [Editorial annotation setup](../deploy/configuration/editorial-preparation.md) for the
runtime extra, exact model artifacts and caption endpoint requirements.

## Inference service

```yaml
advanced:
  inference:
    facts_base_url: ""          # blank: the heads and detectors run in the app process
    timeout_seconds: 60         # one picture, one request; the service answers all producers at once
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
to the service and keeps the two detectors on the app's CPU; the detectors are the cheap half.

When the service does not answer, the run does not stop and does not pretend: the failure is
recorded against the endpoint in the preparation report, which the CLI prints and the cut's
failure detail carries, and with `fallback_to_local: true` the in-process producers take over for the pictures still
missing facts (which needs the model files from `models fetch` on the app box). With it off, the
facts stay missing and the cut refuses until the service is back.

`IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` is the environment form, like every other key.

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
  show_month_dividers: true      # Month changes, limited by the timeline budget
  month_divider_threshold: 2     # Min clips per month when budgeting dividers (1-10)
  use_first_name_only: true      # "Riley" instead of "Riley Smith" in titles
```

The shipped styles have no decorative line accents. The colour palette and custom fonts
are not configurable in the app today.
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

The cache directory holds downloaded media and editorial data. `database` holds run history and automation state; editorial annotations use a separate SQLite file.

```yaml
cache:
  directory: "~/.immich-memories/cache"
  database: "~/.immich-memories/cache.db"
  video_cache_enabled: true      # Cache downloaded videos locally
  video_cache_max_size_gb: 10.0  # Max disk usage for video cache (1-500 GB)
  video_cache_max_age_days: 7    # Auto-delete cached videos older than this (1-365)
  thumbnail_cache_max_size_mb: 10000.0 # Max disk for Immich previews (50 MB-100 GB)
  preview_cache_max_size_mb: 2000.0    # Max disk for clip previews (100 MB-100 GB)
```

The default media budgets total about 22 GB. They are cleanup budgets, not a cap on the app's
disk use: active files can exceed a budget, and annotations, editorial attempts, temporary
renders and output files need more space. Neither the annotation database nor saved attempts
has age-based expiry from these settings.

Size the thumbnail budget for the largest period you plan to prepare. If its working set does
not fit, the cache keeps files in use and logs a warning; later runs may need to download them
again. Use [`runs storage`](../create/cli/runs.md) to inspect usage. See
[storage and backups](../deploy/maintenance/health-logs-cache.md) before deleting anything.

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
[Trigger from Immich or anything else](../create/recipes/trigger-endpoint.md). Keep it out of
`config.yaml` with `IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN`: `server` is not one of the sections
that expand a `${VAR}` reference, so writing one here stores the six literal characters `${VAR}`
as your token. Either way the value is redacted from `/health`, the config viewer, and the logs.
Log redaction is armed at config
load, so the handful of lines printed before the config exists (startup, a config file that
fails to parse) cannot be covered by it.

`host` is the one value "save" leaves out of `config.yaml` when you never set it. Writing the
`0.0.0.0` default would make the next load treat it as your decision and quietly retire the
localhost bind, which is exactly what older versions did, so a `server.host: 0.0.0.0` already
sitting in your file is ignored with a warning and disappears the next time the file is saved.
Any other address is yours and is kept; so are `--host` and `IMMICH_MEMORIES_SERVER__HOST`, which
nothing but a human ever wrote. To keep a LAN bind with authentication off, use
`allow_unauthenticated_lan: true`.

## Upload to Immich

```yaml
upload:
  enabled: false
  album_name: null               # Created if missing, reused if exists
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

Get notified when auto-generation or scheduled jobs complete. Uses [Apprise](https://github.com/caronc/apprise) for services such as ntfy, Discord, Telegram, Slack, email and webhooks. Apprise ships with the base package, no extra to install. Tier 2: lives under `advanced:` when the app writes the file.

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
