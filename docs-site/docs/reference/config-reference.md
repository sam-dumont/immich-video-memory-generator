---
title: Config Reference
sidebar_label: Config Reference
---

# Config Reference

These options have sane defaults and most users don't need to change them. Add any of these to your `~/.immich-memories/config.yaml` to override. Values shown below are the built-in defaults (placeholders like URLs and example schedules aside).

:::tip Config tiers
Tier 2 sections — `analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `content_analysis`,
`audio_content`, `speech`, `transcription`, `server`, `auth`, `automation`, `notifications` — are
written under an `advanced:` key when the app saves the file:

```yaml
advanced:
  analysis:
    scene_threshold: 25.0
  hardware:
    encoder_preset: "quality"
```

When reading, both placements work; if a section appears in both places the top-level one wins.
Everything else (`immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`,
`trips`, `photos`, `scheduler`) is Tier 1 and stays at the top level.
Unknown keys *inside* a section are silently ignored; unknown top-level keys and invalid values
fail validation at startup.
:::

## Preset

One top-level switch that fills several knobs at once. `fast` is the CPU-only / NAS profile.
Anything you set yourself — a key in the file, an `IMMICH_MEMORIES_…` env var, a CLI flag, a
choice in the web UI — wins over the preset, exactly like `clip_style`.

```yaml
preset: null                       # null | fast
```

`fast` sets, unless you set them yourself: `output.resolution: 1080p`, `output.codec: h264`,
`output.quality: medium`, `hardware.encoder_preset: fast`, `speech.enabled: false` (no per-clip
voice-activity pass), `title_screens.animated_background: false` (static title backgrounds),
`photos.max_ratio: 0.25`; and an analysis depth of `auto` runs as `fast` (favorites first).
The heavy optional features (LLM scoring, music generation, audio-content tagging, transcription)
are already off by default and stay wherever you put them.

Env: `IMMICH_MEMORIES_PRESET=fast`. One-off on the CLI: `immich-memories --preset fast generate …`
(root option, before the subcommand). The settings page names the active preset; Step 3 defaults
its resolution to the preset's and says so.

Caveat: the settings page's "save" writes every value to `config.yaml`, after which they all count
as "set by you" — remove the keys you want the preset to own again.

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
  # Clip pacing preset (fills in the five duration params below; explicit values win)
  clip_style: null               # fast-cuts | balanced | long-cuts (null = use individual values)

  # Scene detection
  scene_threshold: 27.0          # Scene change sensitivity (1-100, lower = more scenes)
  min_scene_duration: 1.0        # Minimum scene length in seconds (0.5-10)
  use_scene_detection: true      # Use scene detection for natural cut points

  # Clip duration tuning (or use clip_style above)
  max_segment_duration: 15.0     # Long scenes get subdivided (2-30s)
  min_segment_duration: 2.0      # Clips shorter than this are discarded (0.5-5s)
  optimal_clip_duration: 5.0     # Sweet spot clip duration (2-15s)
  max_optimal_duration: 10.0     # Max optimal duration for long sources (5-30s)
  target_extraction_ratio: 0.15  # Target ratio of clip to source (0.15 = use 15%; 0.05-0.5)

  # Duplicate detection
  duplicate_hash_threshold: 8    # Perceptual hash threshold (0-64)
  min_source_short_side: 1080    # Drop smaller clips unless they carry camera EXIF
  subject_policy_enabled: true   # Prefer clips of people over things
  max_animal_ratio: 0.10         # Share of the video that may be animal clips
  max_object_ratio: 0.05         # Share that may be object clips (must also score well)

  # Performance
  download_workers: 3            # Parallel download clients for video and thumbnail prefetching (1-8)
  enable_downscaling: true       # Downscale for analysis (~3-5x faster)
  analysis_resolution: 480       # Target height for analysis (240-1080)

  # Live Photos (iPhone 3s video clips)
  include_live_photos: true      # Include Live Photo clips (ON by default)
  live_photo_merge_window_seconds: 10.0  # Max gap to group as burst (1-60s)

  # Audio-aware boundaries
  use_unified_analysis: true     # Avoid mid-sentence cuts
  cut_point_merge_tolerance: 0.5 # Window for merging nearby boundaries (0.1-2s)
  silence_threshold_db: -40.0    # Silence detection threshold (-60 to -10 dB)
  min_silence_duration: 0.3      # Minimum silence gap duration (0.1-1s)
```

Presets: `fast-cuts` = 3–6 s clips, 30% extraction; `balanced` = 5–10 s, 40%; `long-cuts` =
8–15 s, 50%. With no preset the defaults above apply (5–10 s, 15%). Any Live Photo cluster of two
or more within the merge window is treated as a burst — the count is not configurable.

## Generation defaults

```yaml
defaults:
  scale_mode: "blur"             # fit, fill, smart_crop, blur (used when --scale-mode is not given)
  transition: "smart"            # cut, crossfade, smart, none (used when --transition is left on smart)
  transition_duration: 0.5       # 0-2 seconds
```

Target duration and orientation are chosen per run — the UI slider / `--duration` (seconds) and
`--orientation` — with the memory type preset supplying the default duration; there is no config
default for either. The target duration describes the finished video, not just the selected source
clips. The planner budgets opening/title/ending cards and subtracts the expected overlap from fades
before it sets the content budget. Month dividers use an all-or-none policy, and trip location cards
are counted only after the final media selection. If filtering leaves usable time on the table, the
optimizer backfills eligible leftovers and can relax the preferred photo ratio; hard eligibility and
deduplication rules remain enforced. Frame and transition boundaries can leave the encoded result
less than one transition away from the requested duration.

## Output

```yaml
output:
  directory: "~/Videos/Memories"
  format: "mp4"                  # mp4 or mov
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: h264                     # h264 (default), h265 (HDR-capable), prores
  hdr_mode: auto                  # auto, sdr, hdr
  quality: "high"                # high, medium, low (shorthand for CRF presets)
  crf: null                      # unset = derived from quality; 0-51 overrides (lower = better)
```

CRF is the image-quality authority. `quality` is only a shorthand used when `crf` is omitted;
an explicit `crf` wins. Software H.264/H.265 encoders receive CRF directly. FFmpeg's
VideoToolbox encoders do not implement CRF, so the app translates the same 0-51 setting to
VideoToolbox's 1-100 quality scale (for example, CRF 18 becomes `-q:v 75`). Lower CRF still means
higher quality for software H.264/H.265 and Apple VideoToolbox. NVENC, VAAPI, QSV, and ProRes use
their existing backend policies; `output.crf` is not currently translated for those encoders.

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
  max_ratio: 0.50               # Max 50% of clips can be photos (0-1)
  duration: 4.0                  # Seconds per photo clip (1-10)
  moment_gap_seconds: 120        # Window for "same moment as a video" (0-3600)
  moment_hash_threshold: 10      # Hash bits allowed between photo and that video (0-64)
  score_penalty: 0.2             # Photos score 80% of equivalent videos (0-1)
```

The animation per photo (Ken Burns, face pan, blurred background) is picked automatically from the
photo's content; it is not configurable.

## Hardware acceleration

```yaml
hardware:
  enabled: true                  # false = CPU encoding, no GPU probing at all
  encoder_preset: "balanced"     # fast, balanced, quality
  gpu_analysis: true             # CUDA frame differencing for scene analysis when available
  gpu_decode: true               # Hardware video decoding
```

The backend is detected automatically (NVIDIA NVENC → Apple VideoToolbox → Intel QSV → VAAPI, first
hit wins); there is no override. `hardware.enabled: false` is the only way to force CPU. On multi-GPU
Linux hosts pick the card with `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES`.

`encoder_preset` controls encoder speed/effort; it does not replace `output.crf`. On Apple,
`fast` enables VideoToolbox's speed-priority mode while `balanced` and `quality` leave it disabled.
Image quality still comes from the CRF translation described above.

## Audio and music

Background music is generated when `ace_step.enabled` or `musicgen.enabled` is on. With both on,
ACE-Step generates and the MusicGen server is used only for stem separation (ducking); with neither,
stems come from a local Demucs install if present. Per run you can still override that:
`--music PATH` uses your own file, `--no-music` skips music, and the UI offers None / Upload file /
AI Generated in Step 3. Music volume is a per-run setting too (`--music-volume` or the UI slider);
ducking under speech and the 2 s / 3 s fades are fixed.

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
  provider: "openai-compatible"   # openai-compatible or ollama
  base_url: "http://localhost:8080/v1"
  model: ""                        # e.g. mlx-community/Qwen2.5-VL-7B-Instruct-8bit
  api_key: ""                      # optional, only for cloud APIs
  timeout_seconds: 300             # increase for slow local models (10-3600)
```

A separate `title_llm` section can point the web UI's title step at a different model than the one
used for content analysis:

```yaml
title_llm:
  provider: "openai-compatible"
  base_url: "http://localhost:11434/v1"
  model: "llama3.2"
  api_key: ""
  timeout_seconds: 300
```

The switch is all-or-nothing on `title_llm.model`: when it is set the whole `title_llm` block is
used, and any field you leave out takes the *built-in* default (`provider: openai-compatible`,
`base_url: http://localhost:8080/v1`, empty `api_key`) — it is not inherited from `llm`. When
`title_llm.model` is empty, `llm` is used. CLI title generation always uses `llm`.

## Content analysis (LLM-based scoring)

```yaml
content_analysis:
  enabled: false
  weight: 0.35                   # Score weight (0-1)
  analyze_frames: 2              # Frames per segment (1-4)
  min_confidence: 0.5
  frame_max_height: 480
  openai_image_detail: "low"     # low (85 tokens) or high (1889 tokens)

audio_content:
  enabled: false
  weight: 0.15
  use_panns: true                # Semantic labels via optional audio-ml extra
  min_confidence: 0.3
  laughter_confidence: 0.1       # Lower threshold for laughter/baby sounds (0.1-0.5)
  laughter_bonus: 0.1            # Score added to a segment with laughter (0-0.3)
  protect_laughter: true         # Avoid cutting through laughter events
  protect_speech: true           # Avoid cutting through speech regions
```

`use_panns: true` uses PANNs to label laughter, babies, speech, music, cheering, engines, and
other AudioSet events. Install it with `uv sync --extra audio-ml` or
`pip install 'immich-memories[audio-ml]'`. If the extra is missing, generation continues with the
energy-only analyzer. That fallback can find loud and quiet structure, but it cannot reliably tell
laughter from speech, music, or background noise.

## Speech boundaries

```yaml
speech:
  enabled: true
  vad_threshold: 0.25            # Frame speech probability that counts as voice (0.1-0.9)
  min_silence_ms: 200            # Silence needed to close a speech region (50-2000)
```

Requires the `speech` extra (`uv sync --extra speech`, included in `all` and `all-mac`). The
FireRedVAD weights ship inside the package — nothing is downloaded at runtime.

Without voice activity, protected ranges come from PANNs, which merges contiguous same-class
frames into one span: a noisy clip becomes a single protected range covering everything and
boundary adjustment has nowhere to move, so the clip stays at full duration. Voice activity
keeps the pauses between utterances, giving cuts somewhere to land.

Speech boundaries do not require `audio_content.enabled`. Voice activity needs only the audio
track and the bundled model, so cut placement works on a default install; enabling
`audio_content` adds event *scoring* on top, and the two degrade independently.

Laughter, singing, cheering and applause protection still comes from PANNs (`audio_content`
above) — the voice detector does not fire on them, so that protection needs `audio_content`
switched on.

`vad_threshold` is below FireRedVAD upstream's 0.4 on purpose: measured across 143 clips, 0.25
detected speech in 49 more of them with no false positives on clips below -40 dBFS. Raise it if
background chatter is being protected; lower it if quiet speech is being cut through.

`min_silence_ms` does double duty: it is the pause width that closes a speech region, and it
caps how far each protected range is widened before boundary adjustment. Widening by half that
pause or more would merge the regions back together and undo the split.

Set `enabled: false` to turn voice activity off entirely — there is no alternative engine.

## Transcription

```yaml
transcription:
  enabled: false                 # Transcribe speech in the top candidate clips
  languages: []                  # Languages your library contains, e.g. [fr, en]
  model: medium                  # tiny / base / small / medium / large, or a path
  min_voiced_seconds: 1.0        # Voice activity required before transcribing
  min_confidence: 0.0            # Mean token probability floor (see below)
  use_gpu: true                  # Metal on macOS; Linux wheels are CPU-only
```

Requires the `transcribe` extra (`uv sync --extra transcribe`, included in `all` and `all-mac`)
and `speech.enabled: true` — voice activity is what decides whether a clip is transcribed at all,
so with speech off there is no gate and nothing is transcribed.

`languages` is the one setting you have to fill in. Leave it empty and nothing is transcribed,
which is deliberate: automatic detection across all 99 languages put French audio in Japanese and
in German on both attempts, and a transcript in the wrong language is worse than no transcript.
One entry forces that language and skips detection entirely. Several restrict detection to those
languages, so the model chooses between the two or three your library actually contains instead of
guessing among 99.

Transcripts are stored on the top five candidate segments of each video and **do not affect any
score**. Nothing reads them yet.

Unlike the FireRedVAD weights, which ship inside the package, whisper models are downloaded from
HuggingFace on first use — about 1.5 GB for the `medium` default. In Docker, mount the model
directory as a volume or every container start downloads it again. Set `model: base` (~148 MB) if
that download matters more to you than accuracy; measured on real family audio, `base` returned
fragments where `medium` returned whole sentences.

### A 30-second window, not the clip

Whisper is transcribed over a **30-second window centred on the clip**, not the clip itself. It is
trained on 30-second windows and pads shorter input with silence, which triggers hallucination: on
short slices the same moments returned "- Dear." and "La papa." where a full window returned
"Il est mignon. Tu veux lui faire une petite douce ? Pas la tête, pas le ventre."

The stored transcript is therefore speech heard *around* the clip, and neighbouring candidates of
one moment share it. Audio context distinguishes between videos, not between the top candidates
of a single video.

`min_voiced_seconds` is still measured on the clip itself — the question "is there speech here"
is unchanged, only the audio handed to the model widens.

### What the gate can and cannot catch

Measured over 80 clips and 282 candidate segments from a real family library:

| | |
|---|---|
| Clips with no voice activity at all | 9% |
| Candidate segments declined before reaching whisper | 71% |
| Whisper calls saved by reusing overlapping candidates | 46% |
| Cost per segment, `medium` | ~0.6 s |

`min_voiced_seconds` does most of the filtering. Surviving transcripts are also rejected if they
are a repetition loop — whisper emitting one phrase several times over — or contain no words at
all, such as the `...` it returns on digital silence. Both arrive at confidence 0.83 and above and
so are invisible to `min_confidence`.

`min_confidence` defaults to **0.0** because the signal is inverted on this audio: correct
transcripts measured 0.63–0.71 while fluent nonsense measured 0.84–0.95. Raising the floor removes
good transcripts before bad ones. It stays configurable if your library is quieter than a house
with children in it.

The signal that would separate the two is `no_speech_prob`, and whisper.cpp does not expose it:
the getter exists in the C API but neither the CLI's JSON output nor the Python bindings surface
it.

### What the transcript is used for

Transcripts are given to the vision model alongside the frames, marked as possibly inaccurate,
with an instruction to ignore them when they do not match the image. The vision model is the only
component that sees both, so it is the only available check on a wrong transcript — and it works:
a clip whose transcript was about refuelling a car, over footage of a beach, produced exactly the
same description as the frames alone.

Because the model reads the speech, spoken names can end up in the stored description. Enabling
transcription therefore changes what the analysis cache records about the people in your videos.

Turning transcription on changes LLM-derived scores, so it bumps the scoring version and cached
scores are recomputed.

## Title screens

```yaml
title_screens:
  enabled: true                  # Opening title, month dividers and ending screen
  title_duration: 3.5            # seconds (1-10)
  month_divider_duration: 2.0    # seconds (1-5)
  ending_duration: 7.0           # seconds (2-15)
  locale: "auto"                 # en, fr, or auto-detect
  style_mode: "auto"             # auto (mood-based) or random
  show_month_dividers: true      # When the video spans several months (all-or-none)
  month_divider_threshold: 2     # Min clips in a month to show its divider (1-10)
  use_first_name_only: true      # "Alice" instead of "Alice Smith" in titles
```

Look-and-feel (animated backgrounds, decorative lines, colour palette, custom fonts) is not
configurable from the config file today. The `immich-memories titles` command exposes some of these
as flags for previewing.

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
  thumbnail_cache_max_size_mb: 500.0   # Max disk for Immich thumbnails (50 MB-100 GB)
  preview_cache_max_size_mb: 2000.0    # Max disk for clip previews (100 MB-100 GB)
```

The video cache defaults to 10 GB. If you're tight on disk, lower `video_cache_max_size_gb` or disable it entirely with `video_cache_enabled: false`.

Thumbnails and clip previews are derived from your library and are cheap to rebuild, so they get their own smaller budgets. Each is evicted least-recently-used once it exceeds its limit — a file you keep opening keeps earning its place.

## Server (UI)

```yaml
server:
  host: "0.0.0.0"               # Listen address (use 127.0.0.1 to restrict to localhost)
  port: 8080                     # Listen port (1-65535)
  enable_demo_mode: false        # Show the demo/privacy (blur) toggle in the sidebar
  secure_cookies: false          # Mark the session cookie Secure (turn on behind an HTTPS reverse proxy)
```

These can also be set via CLI flags: `immich-memories ui --host 127.0.0.1 --port 9090`.

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
  job_timeout_minutes: 60  # Max time per job before timeout (increase for large libraries)
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

## Smart automation

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
