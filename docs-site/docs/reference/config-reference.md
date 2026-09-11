---
title: Config Reference
sidebar_label: Config Reference
---

# Config Reference

These options have sane defaults and most users don't need to change them. Add any of these to your `~/.immich-memories/config.yaml` to override. Values shown below are the built-in defaults (placeholders like URLs and example schedules aside).

:::tip Config tiers
Tier 2 sections — `analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`,
`automation`, `notifications`, `triage`, `editorial` — are
written under an `advanced:` key when the app saves the file:

```yaml
advanced:
  analysis:
    max_album_assets: 5000
  hardware:
    encoder_preset: "quality"
```

When reading, both placements work; if a section appears in both places the top-level one wins.
Everything else stays at the top level: `immich`, `defaults`, `output`, `audio`, `title_screens`,
`cache`, `upload`, `trips`, `photos`, `preset`, and the two the loader calls internal rather than
Tier 1, `scheduler` and `title_llm`. The rule is mechanical — anything outside the Tier 2 set is
left where it is.
Unknown keys *inside* a section are silently ignored, with one exception: the keys of the removed
clip scorer (`analysis.max_refinement_passes`, `photos.max_ratio`, the whole `content_analysis`,
`audio_content`, `speech` and `transcription` sections, and the rest of that family). A file that
still sets one is refused at startup with a message naming the key, because a setting that loads
and does nothing is worse than one that fails. Unknown top-level keys and invalid values fail
validation at startup.
:::

## Preset

One top-level switch that fills several knobs at once. `fast` is the CPU-only / NAS profile.
Anything you set yourself — a key in the file, an `IMMICH_MEMORIES_…` env var, a CLI flag, a
choice in the web UI — wins over the preset.

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
fields it appears to be about — after which they all count as "set by you", and the preset has
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
  exclude_stills_without_camera_exif: true   # a photo naming no camera was received, not shot
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
  live_photo_min_clip_seconds: 3.5       # Below this a burst ships as a photo (0-30s)
```

Any Live Photo cluster of two or more within the merge window is treated as a burst — the count
is not configurable. Where a clip is cut, and how long it runs, is the editor's decision per
carrier; there is no pacing preset any more.

`max_album_assets` applies per media type, so the default reads up to 10,000 videos and 10,000
photos from one album. Smart albums reach tens of thousands; Immich returns newest first, so a
bigger album is truncated to its most recent assets and you get a warning naming it. Narrow the
album, raise the cap, or use a date range instead.

## Generation defaults

```yaml
defaults:
  scale_mode: "blur"             # blur | fit (black bars) — used when --scale-mode is not given
  transition: "smart"            # cut, crossfade, smart, none (used when --transition is left on smart)
  transition_duration: 0.5       # 0-2 seconds
```

Target duration and orientation are chosen per run — the UI slider / `--duration` (seconds) and
`--orientation` — with the memory type preset supplying the default duration; there is no config
default for either. The target duration describes the finished video, not just the selected source
clips. The planner budgets opening/title/ending cards, then adds back the time the fades overlap
away — so the content budget ends up larger than the timeline left over, not smaller. Month
dividers use an all-or-none policy, and trip location cards are counted only after the final media
selection. If filtering leaves usable time on the table, the optimizer backfills eligible leftovers
and can relax the preferred photo ratio; hard eligibility and deduplication rules remain enforced.
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
[the hardware overview](../deploy/hardware/overview.md#quality-what-crf-means-on-each-backend)
for the measured table. Lower CRF still means higher quality everywhere.

The presets are points on that curve, measured on 1080p60 film and — for `balanced` — judged by
eye on gradients:

| `quality` | reference CRF | SSIM | software bitrate | per minute |
|---|---|---|---|---|
| `high` | 18 | 0.99169 | 4.6 Mbps | ~35 MB |
| `balanced` (default) | 24 | 0.98451 | 1.6 Mbps | ~12 MB |
| `fast` | 24 | 0.98451 | 1.6 Mbps | ~12 MB, encoded as fast as the backend can |

`high` used to mean CRF 12, which is past SSIM 0.999 — quality nobody can see, at several times
the bits, and the reason exports were hundreds of megabytes.

There is deliberately no tier below `balanced`: around 0.980 gradients start to band, and a preset
that visibly breaks up a sky is not worth a few megabytes. `fast` keeps the balanced picture and
buys its speed from the encoder effort preset instead, overriding `hardware.encoder_preset`.
`medium` and `low` are retired names that still load, resolving to `balanced` and `fast`.

`codec_policy` decides what happens when the machine has no hardware encoder for the codec you
asked for but does have one for the other. `prefer_hardware` (the default) switches codec and says
so in the log and the run record, which on a chip like Intel Gemini Lake — H.264 encode entrypoint,
no HEVC one — is the difference between a film finishing and the CPU doing all of it. The file is
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

Burst de-duplication keeps only the best-scored frame of a run of near-identical photos, so the
fifteen shots of the same jump do not become fifteen clips. `burst_window_seconds: 0` all but turns
it off: photos sharing an identical timestamp still group.

## Hardware acceleration

```yaml
hardware:
  enabled: true                  # false = CPU encoding, no GPU probing at all
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

Background music needs `ace_step.enabled` or `musicgen.enabled`. With neither, the pipeline refuses
to build rather than running silent. With both on, ACE-Step generates, and MusicGen is both the
fallback generator and the stem separator used for ducking. With MusicGen off, stems come from a
local Demucs install if there is one. Per run you can still override that: `--music PATH` uses your
own file, `--no-music` skips music, and the options page in the UI offers None / Upload file / AI Generated,
plus Bundled when the `music` extra is installed. Music volume is a per-run setting too
(`--music-volume` or the UI slider); ducking under speech and the 2 s / 3 s fades are fixed.

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
picks music from it on its own — pass the file with `--music`.

## LLM (vision model)

Used by content analysis and title generation. Any OpenAI-compatible endpoint works: mlx-vlm, Ollama, vLLM, Groq, OpenAI itself.

```yaml
llm:
  provider: "openai-compatible"   # openai-compatible | openai | zai | anthropic | ollama
  base_url: "http://localhost:8080/v1"
  model: ""                        # e.g. mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit
  api_key: ""                      # optional, only for cloud APIs
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

**The goal of this product is a fully local process** — your photos analyzed
on your own hardware, nothing leaving your network. A local server (mlx,
vLLM, Ollama) is the intended setup. The cloud providers below exist for one
reason: so that people without the means — or the desire — to run a local
model can still use the product. Using one sends each analyzed clip's frames
or thumbnails and the derived descriptions to that provider; see the
"data leaving your network" page before choosing this route.

Two adapters cover every provider: `openai-compatible` speaks
`/chat/completions` (vLLM, mlx, Ollama's `/v1`, aggregators, OpenAI itself),
and `anthropic` speaks the native `/v1/messages` API (Claude, or z.ai's
Anthropic-compatible endpoint) with its own reasoning dialect handled
natively. `openai` and `zai` are named presets: the generic adapter with the
provider's URL and reasoning dialect pre-filled — set `provider: openai`,
`model: gpt-5.6-terra` and an API key, and thinking works with nothing else
to configure. Explicit `base_url`/`thinking_params` always win over a preset.

`thinking: true` runs the model in reasoning mode for the judgement calls
only — the holistic selection review, title generation, and the special-day
question in `discover-days`. Measured on the live endpoint, a thinking call
ran 30-134 s where the same model answered in 4-7 s without it, and it needs
a 4000-token ceiling to finish reasoning — which matters on a paid API. Bulk
work (per-clip content analysis, photo scoring) always runs in fast mode:
reasoning over multiple images is unreliable on current models, and the volume
would make it unaffordable anyway.

`thinking_params` is merged verbatim into a thinking request, so the switch
matches your server's dialect: the default is Qwen's
`chat_template_kwargs: {"enable_thinking": true}` (vLLM, mlx, SGLang); for
the OpenAI API use `{"reasoning_effort": "medium"}`. Leave `thinking` off
unless you know the server supports your chosen switch — some
OpenAI-compatible servers reject unknown request fields.

`no_thinking_params` is the other half, and it matters on servers whose chat
template reasons by default: not asking for reasoning is not the same as
asking for none, so bulk analysis reasons anyway, at the small token budget
those calls ask for, and comes back truncated mid-thought with nothing
parseable in it. This field is sent on every non-thinking call — it hangs off
the switch, not off `thinking`, because a server that reasons by default does
so whether or not you turned reasoning on. The default is Qwen's
`chat_template_kwargs: {"enable_thinking": false}`; set it to `{}` for servers
that reason only when asked (the `openai` and `zai` presets already do). A
server that rejects the field is detected from its 400 and asked without it
from then on.

`send_image_detail` covers one more dialect gap: OpenAI's optional
`image_url.detail` field is sent by default, and some strict vision schemas
accept only `image_url.url` and reject requests carrying anything more. Set it
to `false` for those servers — the `zai` preset already does.

Parameter dialects are otherwise handled automatically: OpenAI's reasoning
models (gpt-5 family) reject `max_tokens` and non-default temperatures, and
the query layer reads those 400s, adapts the request, and remembers the
answer per server and model — validated against the live OpenAI API. One
provider note: z.ai's OpenAI-compatible endpoint accepts image content only
on its dedicated vision models, so point `llm.model` at one of those if you
use it for content analysis.

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
options block rather than replacing it — content analysis already asks for a
4096-token window that way, since Ollama's 2048 default does not hold the
prompt plus several frames.

A separate `title_llm` section can point title generation at a different model than the one used for
content analysis. It applies to the CLI and the web UI alike:

```yaml
title_llm:
  provider: "openai-compatible"
  base_url: "http://localhost:8080/v1"
  model: "llama3.2"              # example; the default is empty, which means "use llm"
  api_key: ""
  timeout_seconds: 300
  send_image_detail: true        # same switch as llm.send_image_detail
```

The switch is all-or-nothing on `title_llm.model`: when it is set the whole `title_llm` block is
used, and any field you leave out takes the *built-in* default (`provider: openai-compatible`,
`base_url: http://localhost:8080/v1`, empty `api_key`) — it is not inherited from `llm`. When
`title_llm.model` is empty, `llm` is used. Both entry points resolve it the same way.

## Triage heads

```yaml
triage:
  enabled: false                 # Legacy standalone triage hook; editorial preparation runs independently
  encoder: ~/.immich-memories/models/triage/dinov2-small.onnx  # DINOv2-small ONNX export (88 MB)
  encoder_url: https://github.com/...    # where `models fetch` downloads that export from
  bundle: ""                     # Head weights (.npz); empty = the public bundle in the package
```

Editorial preparation uses `triage.encoder` with the public six-head bundle configured under
`editorial.preparation.head_bundle`. Install the `editorial` extra and provide the pinned
DINOv2-small ONNX export. Its digest is checked on load. Missing required head facts stop
selection; `triage.enabled: false` does not bypass preparation. The separate legacy triage
hook still uses `triage.bundle` and `triage.db`.

The public heads provide context. They do not train on your library or independently decide
whether a picture is suitable for the audience.

## Editorial planner

```yaml
editorial:
  annotation_database: ""        # defaults to annotations.sqlite inside the configured cache directory
  description_model: "smolvlm2-500m-base-public@envelope-v3-compact"
  pixel_producer_key: "pixel-facts-v1"  # exact producer of pixel facts and thresholds  # gitleaks:allow
  head_versions:                 # exact producer version selected for each annotation head
    activity: public-v1
    children: public-v1
    doc_docling: det-v1
    location: public-v1
    nsfw_marqo: det-v1
    people: public-v1
    swim: oi-v3
    venue: oi-v3
  preparation:
    caption_base_url: http://localhost:8092/v1
    caption_timeout_seconds: 90
    caption_concurrency: 4
    batch_size: 32
    head_bundle: ""              # packaged public six-head bundle
    detector_python: ""          # current Python interpreter
    detector_cache_dir: ""       # normal Hugging Face Hub cache
    allow_model_downloads: false
```

Tier 2 — lives under `advanced:` when the app writes the file. Story-first selection is the
production route for UI, CLI and scheduled runs. Old `enabled` and `story_first` keys are
ignored; there is no opt-in flag or environment switch.

The planner reads the whole source period, identifies its stories and distinct moments,
then allocates duration and picks representations of those moments. A longer target can
show more of a story without inventing more events from near-duplicate pictures.

New runs use the **FAMILY** audience. Ordinary family material, including a shirtless baby,
baby bath time, breastfeeding or a parent holding a newborn in hospital, can be considered.
Graphic medical procedures, sexual content, exposed adult changing and identifying records
remain excluded.

Preparation fills missing descriptions, public heads, detectors and pixel measurements in
the annotation database. Complete facts skip provider calls. Missing previews or providers
stop selection with an explicit incomplete result. Two verified invalid caption completions
can be recorded as `caption unavailable`, counted separately from successful descriptions.
See [Editorial annotation setup](../deploy/configuration/editorial-preparation.md) for the
runtime extra, exact model artifacts and caption endpoint requirements.

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
  use_first_name_only: true      # "Alice" instead of "Alice Smith" in titles
```

`animated_background` and `show_decorative_lines` are all the look-and-feel the config file
exposes; the colour palette and custom fonts are not configurable today.
`animated_background: false` keeps the gradient still
— no rotation, colour pulse or vignette pulse — which is what `preset: fast` selects. The
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

Controls where analysis results and downloaded videos are stored. The video cache avoids re-downloading from Immich on repeated runs.

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

The video cache defaults to 10 GB. If you're tight on disk, lower `video_cache_max_size_gb` or disable it entirely with `video_cache_enabled: false`.

### Size the thumbnail cache by your library, not by taste

`thumbnail_cache_max_size_mb` is the one cache budget that scales with how big your library is. Every candidate asset in a memory's scope gets an Immich preview fetched and read back several times — sharpness and exposure, the DINOv2 heads, the contact sheets, the caption. Measured on a real library, one preview is about **315 KB**, so:

```
budget in MB ≈ 0.35 × (assets a memory's scope can reach)
```

One 10,793-candidate scope wants about 3.4 GB; the `0.35` leaves a little headroom over the measured 0.315 MB. The 10 GB default holds roughly 31,000 previews, which covers three scopes that size.

If the run's working set does not fit, nothing is lost mid-run — previews this run is still using are never deleted, so the cache temporarily overflows the limit instead. But the *next* run reclaims them, so the next overlapping memory re-downloads every preview and re-captions the assets whose banked caption failure no longer matches the bytes. You get one `WARNING` per run saying how far over you are and naming this setting. Raise it rather than ignoring it.

The other two budgets are not library-sized and need no such rule: `preview_cache_max_size_mb` holds the video renditions the wizard's player streams (one cut's clips), and the video cache holds the originals being assembled (also one cut's clips). Both are tens of files per run, however big your library is.

It is the full Immich preview that is cached, not a smaller derived tile, even though no single consumer needs 1440 px. Those bytes are the image the model sees in the contact sheets, and their SHA-256 is that sheet's identity; the DINOv2 transform wants a 256 px short side that a 400 px caption tile does not have on 16:9; and both the duplicate-hash bank and the banked caption failures are keyed on them. Caching something smaller would re-derive all of that and change graded output, so it is a re-grade rather than a setting.

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
  allow_unauthenticated_lan: false  # Listen beyond localhost with auth disabled —
                                 # anyone reaching the port can use the UI and the
                                 # Immich library behind it
```

`host` and `port` also have CLI flags: `immich-memories ui --host 127.0.0.1 --port 9090`. The rest
of the section is config-only.

`trigger_token` turns on the HTTP trigger — one POST that runs whatever `auto run` would have
decided, so an Immich workflow (or a cron, or a phone shortcut) can start a memory. See
[Trigger from Immich or anything else](../create/recipes/trigger-endpoint.md). Keep it out of
`config.yaml` with `IMMICH_MEMORIES_SERVER__TRIGGER_TOKEN` or a `${VAR}` reference; either way it
is redacted from `/health`, the config viewer, and the logs. Log redaction is armed at config
load, so the handful of lines printed before the config exists — startup, a config file that
fails to parse — cannot be covered by it.

`host` is the one value "save" leaves out of `config.yaml` when you never set it. Writing the
`0.0.0.0` default would make the next load treat it as your decision and quietly retire the
localhost bind — which is exactly what older versions did, so a `server.host: 0.0.0.0` already
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

Controls what `immich-memories auto suggest` and `auto run` detect and generate. See the [auto CLI docs](../create/cli/auto.md) for the full command reference. Tier 2 — lives under `advanced:` when the app writes the file.

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

Get notified when auto-generation or scheduled jobs complete. Uses [Apprise](https://github.com/caronc/apprise) (130+ services: ntfy, Discord, Telegram, Slack, email, webhooks). Apprise ships with the base package — no extra to install. Tier 2 — lives under `advanced:` when the app writes the file.

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
