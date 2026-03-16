---
sidebar_position: 4
title: Advanced Configuration
---

# Advanced Configuration

These options have sane defaults and most users don't need to change them. Add any of these to your `~/.immich-memories/config.yaml` to override.

:::tip Config tiers
These advanced sections can be placed under an `advanced:` key in your config file for organization:

```yaml
advanced:
  analysis:
    scene_threshold: 25.0
  hardware:
    backend: "videotoolbox"
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
  include_live_photos: false     # Include Live Photo clips (opt-in)
  live_photo_merge_window_seconds: 10  # Max gap to group as burst (1-60s)
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
  target_duration_minutes: 10    # 1-60 minutes
  output_orientation: "auto"     # auto, landscape, portrait, square
  scale_mode: "smart_crop"       # fit, fill, smart_crop
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
  mode: "lib"                    # lib (local) or api (remote)
  api_url: "http://localhost:8000"
  model_variant: "turbo"         # turbo (fast) or base (quality)
  lm_model_size: "1.7B"
  use_lm: true
  bf16: true
  num_versions: 3
  hemisphere: "north"
  timeout_seconds: 3600
```

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
  show_decorative_lines: true
  avoid_dark_colors: true
  minimum_brightness: 100
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
