---
title: Config Reference
sidebar_label: Config Reference
---

# Config Reference

These options have sane defaults and most users don't need to change them. Add any of these to your `~/.immich-memories/config.yaml` to override.

:::tip Config tiers
These advanced sections can be placed under an `advanced:` key in your config file for organization:

```yaml
advanced:
  analysis:
    scene_threshold: 25.0
  hardware:
    backend: "apple"
```

Or kept at the top level (both formats work).
:::

## Video analysis

```yaml
analysis:
  # Clip pacing preset (overrides individual duration params below)
  clip_style: null               # fast-cuts | balanced | long-cuts (null = use individual values)

  # Scene detection
  scene_threshold: 27.0          # Scene change sensitivity (1-100, lower = more scenes)
  min_scene_duration: 1.0        # Minimum scene length in seconds
  use_scene_detection: true      # Use scene detection for natural cut points

  # Clip duration tuning (or use clip_style above)
  max_segment_duration: 15.0     # Long scenes get subdivided (2-30s)
  min_segment_duration: 2.0      # Clips shorter than this are discarded (0.5-5s)
  optimal_clip_duration: 5.0     # Sweet spot clip duration (2-15s)
  max_optimal_duration: 15.0     # Max optimal duration for long sources (5-30s)
  target_extraction_ratio: 0.25  # Target ratio of clip to source (0.25 = use 25%)

  # Duplicate detection
  duplicate_hash_threshold: 8    # Perceptual hash threshold (0-64)
  keyframe_interval: 1.0         # Seconds between keyframe extractions

  # Performance
  enable_downscaling: true       # Downscale for analysis (~3-5x faster)
  analysis_resolution: 480       # Target height for analysis (240-1080)

  # Live Photos (iPhone 3s video clips)
  include_live_photos: true      # Include Live Photo clips (ON by default)
  live_photo_merge_window_seconds: 10.0  # Max gap to group as burst (1-60s)
  live_photo_min_burst_count: 3  # Min photos for burst merging (2-20)

  # Audio-aware boundaries
  use_unified_analysis: true     # Avoid mid-sentence cuts
  cut_point_merge_tolerance: 0.5 # Window for merging nearby boundaries (0.1-2s)
  silence_threshold_db: -40.0    # Silence detection threshold (-60 to -10 dB)
  min_silence_duration: 0.2      # Minimum silence gap duration (0.1-1s)
```

## Generation defaults

```yaml
defaults:
  target_duration_seconds: 600   # 10-3600 seconds
  output_orientation: "auto"     # auto, landscape, portrait, square
  scale_mode: "blur"             # fit, fill, smart_crop, blur
  transition: "smart"            # cut, crossfade, smart, none
  transition_duration: 0.5       # 0-2 seconds
  transition_buffer: 0.5         # Extra footage around clips for smooth fades
```

## Output

```yaml
output:
  directory: "~/Videos/Memories"
  format: "mp4"                  # mp4 or mov
  resolution: "1080p"            # 720p, 1080p, 4k
  codec: "h264"                  # h264, h265, prores
  crf: 18                        # Quality (0-51, lower = better)
  quality: "high"                # high, medium, low (shorthand for CRF presets)
```

## Photos

```yaml
photos:
  enabled: true                  # Include photos in memories
  max_ratio: 0.50               # Max 50% of clips can be photos
  duration: 4.0                  # Seconds per photo clip
  animation_mode: auto           # auto | ken_burns | face_zoom | blur_bg
  enable_collage: true           # Group series as collages
  series_gap_seconds: 60         # Max gap to group as series
  collage_duration: 6.0          # Seconds per collage clip (2-15s)
  zoom_factor: 1.15              # Ken Burns zoom amount (15%)
  score_penalty: 0.2             # Photos score 80% of equivalent videos
```

## Hardware acceleration

```yaml
hardware:
  enabled: true
  backend: "auto"                # auto, nvidia, apple, vaapi, qsv, none
  encoder_preset: "balanced"     # fast, balanced, quality
  device_index: 0                # GPU index for multi-GPU systems
  gpu_analysis: true             # Use GPU for video analysis
  gpu_decode: true               # Hardware video decoding
  gpu_memory_limit: 0            # GPU memory limit in MB (0 = unlimited)
```

## Audio and music

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

musicgen:
  enabled: false
  base_url: "http://localhost:8000"
  api_key: ""
  timeout_seconds: 10800         # 3 hours
  num_versions: 3
  hemisphere: "north"

ace_step:
  enabled: false
  mode: "api"                    # api (remote REST server) or lib (local, requires Python 3.12)
  api_url: "http://localhost:8000"
  model_variant: "turbo"         # turbo (fast) or base (quality)
  lm_model_size: "1.7B"
  use_lm: true
  bf16: true
  num_versions: 3
  hemisphere: "north"
  timeout_seconds: 3600
```

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

A separate `title_llm` section can override these for trip title generation (useful if you want a different model for titles vs. content analysis):

```yaml
title_llm:
  base_url: "http://localhost:11434/v1"
  model: "llama3.2"
```

Any field not set in `title_llm` falls back to the `llm` values.

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
  use_panns: true                # PANNs ML model (requires torch)
  min_confidence: 0.3
  laughter_confidence: 0.2
  laughter_bonus: 0.1
  protect_laughter: true
  protect_speech: true
```

## Title screens

```yaml
title_screens:
  enabled: true
  title_duration: 3.5
  month_divider_duration: 2.0
  ending_duration: 7.0
  animation_duration: 0.5
  locale: "auto"                 # en, fr, or auto-detect
  style_mode: "auto"             # auto (mood-based) or random
  animated_background: true
  show_decorative_lines: false
  avoid_dark_colors: false
  minimum_brightness: 0
  show_month_dividers: true
  month_divider_threshold: 2
  use_first_name_only: true
  custom_font_path: null
```

## Trip detection

```yaml
trips:
  homebase_latitude: 0.0
  homebase_longitude: 0.0
  min_distance_km: 50
  min_duration_days: 2
  max_gap_days: 2
```

## Scoring priority

Three knobs that control how clips get ranked. Each can be `low`, `medium`, or `high`.

```yaml
scoring_priority:
  people: high                   # Favor clips with recognized faces
  quality: medium                # Favor stable, well-lit footage
  moment: medium                 # Favor interesting content (motion, events)
```

`people: high` means clips with faces get a large score boost. Useful for family compilations. Set it to `low` if you're doing a landscape/travel memory where faces aren't the point.

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
```

The video cache defaults to 10 GB. If you're tight on disk, lower `video_cache_max_size_gb` or disable it entirely with `video_cache_enabled: false`.

## Server (UI)

```yaml
server:
  host: "0.0.0.0"               # Listen address (use 127.0.0.1 to restrict to localhost)
  port: 8080                     # Listen port (1-65535)
  enable_demo_mode: false        # Enable privacy/demo mode permanently
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

Controls what `immich-memories auto suggest` and `auto run` detect and generate. See the [auto CLI docs](../create/cli/auto.md) for the full command reference.

```yaml
automation:
  cooldown_hours: 24              # min hours between auto-generated memories
  upload_to_immich: false         # auto-upload results
  album_name: null                # target album for uploads
  detect_monthly: true            # monthly highlights candidates
  detect_yearly: true             # year-in-review candidates
  detect_trips: true              # GPS trip detection (needs homebase coords)
  detect_person_spotlight: true   # per-person highlight candidates
  detect_activity_burst: true     # unusually active months
  burst_threshold: 2.0            # multiplier above rolling average to trigger burst
```

## Notifications

Get notified when auto-generation or scheduled jobs complete. Uses [Apprise](https://github.com/caronc/apprise) (130+ services: ntfy, Discord, Telegram, Slack, email, webhooks).

Install: `pip install immich-memories[notifications]`

```yaml
notifications:
  enabled: false
  urls:                           # Apprise notification URLs
    - "ntfy://ntfy.sh/my-topic"
    - "discord:///webhook_id/token"
    - "tgram://bot_token/chat_id"
  on_success: true                # notify on successful generation
  on_failure: true                # notify on failed generation
```

Test your config: `immich-memories auto test-notification`
