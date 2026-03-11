---
sidebar_position: 1
title: Config File
---

# Config File

Location: `~/.immich-memories/config.yaml`

The config file is created automatically when you first run `immich-memories config`. You can also create it manually. File permissions are set to `600` (owner read/write only) since it contains API keys.

## Environment variable substitution

Any string value supports `${VAR_NAME}` syntax. The variable is expanded at load time:

```yaml
immich:
  api_key: ${IMMICH_API_KEY}

llm:
  api_key: ${OPENAI_API_KEY}
```

## Complete reference

```yaml
# ── Immich server ────────────────────────────────────────────
immich:
  url: "https://photos.example.com"        # Your Immich server URL
  api_key: "${IMMICH_API_KEY}"              # API key (Settings > API Keys in Immich)

# ── Generation defaults ─────────────────────────────────────
defaults:
  target_duration_minutes: 10               # 1-60 minutes
  output_orientation: "auto"                # auto, landscape, portrait, square
  scale_mode: "smart_crop"                  # fit, fill, smart_crop
  transition: "smart"                       # cut, crossfade, smart, none
  transition_duration: 0.5                  # 0-2 seconds
  transition_buffer: 0.5                    # Extra footage (seconds) around clips for smooth fades

# ── Video analysis ───────────────────────────────────────────
analysis:
  scene_threshold: 27.0                     # Scene change sensitivity (1-100, lower = more scenes)
  min_scene_duration: 1.0                   # Minimum scene length in seconds
  duplicate_hash_threshold: 8               # Perceptual hash threshold for duplicate detection (0-64)
  keyframe_interval: 1.0                    # Seconds between keyframe extractions

  # Scene detection
  use_scene_detection: true                 # Use scene detection for natural cut points
  max_segment_duration: 15.0                # Long scenes get subdivided (2-30s)
  min_segment_duration: 2.0                 # Clips shorter than this are discarded (0.5-5s)
  optimal_clip_duration: 5.0                # Sweet spot clip duration (2-15s)
  max_optimal_duration: 15.0                # Max optimal duration for long sources (5-30s)
  target_extraction_ratio: 0.25             # Target ratio of clip to source (0.25 = use 25%)

  # Performance
  enable_downscaling: true                  # Downscale for analysis (~3-5x faster)
  analysis_resolution: 480                  # Target height for analysis (240-1080)

  # Audio-aware boundaries
  use_unified_analysis: true                # Avoid mid-sentence cuts
  cut_point_merge_tolerance: 0.5            # Window for merging nearby boundaries (0.1-2s)
  silence_threshold_db: -40.0              # Silence detection threshold (-60 to -10 dB)
  min_silence_duration: 0.2                 # Minimum silence gap duration (0.1-1s)

# ── Output ───────────────────────────────────────────────────
output:
  directory: "~/Videos/Memories"            # Where generated videos land
  format: "mp4"                             # mp4 or mov
  resolution: "1080p"                       # 720p, 1080p, 4k
  codec: "h264"                             # h264, h265, prores
  crf: 18                                   # Quality (0-51, lower = better, 18 is visually lossless)

# ── Hardware acceleration ────────────────────────────────────
hardware:
  enabled: true                             # Enable GPU acceleration
  backend: "auto"                           # auto, nvidia, apple, vaapi, qsv, none
  encoder_preset: "balanced"                # fast, balanced, quality
  device_index: 0                           # GPU index for multi-GPU systems
  gpu_analysis: true                        # Use GPU for video analysis
  gpu_decode: true                          # Hardware video decoding
  gpu_memory_limit: 0                       # GPU memory limit in MB (0 = unlimited)

# ── LLM provider ────────────────────────────────────────────
llm:
  provider: "openai-compatible"             # ollama or openai-compatible
  base_url: "http://localhost:8080/v1"      # API base URL
  model: ""                                 # Model name (e.g., llava, gpt-4.1-nano)
  api_key: "${OPENAI_API_KEY}"              # API key (only needed for cloud providers)

# ── Audio / music ────────────────────────────────────────────
audio:
  auto_music: false                         # Auto-select background music based on video mood
  music_source: "musicgen"                  # local, musicgen, or ace_step
  local_music_dir: "~/Music/Memories"       # Local music library path

  # Audio ducking (lowers music when speech is detected)
  ducking_threshold: 0.02                   # Voice detection sensitivity (0-1, lower = more sensitive)
  ducking_ratio: 6.0                        # How much to lower music (1-20)
  music_volume_db: -6.0                     # Base music volume (-20 to 0 dB)
  fade_in_seconds: 2.0                      # Music fade in (0-10s)
  fade_out_seconds: 3.0                     # Music fade out (0-10s)

# ── MusicGen API ─────────────────────────────────────────────
musicgen:
  enabled: false                            # Enable AI music generation
  base_url: "http://localhost:8000"         # MusicGen API server URL
  api_key: ""                               # MusicGen API key
  timeout_seconds: 10800                    # Max wait per job (3 hours default)
  num_versions: 3                           # Number of versions to generate (1-5)
  hemisphere: "north"                       # north or south (for seasonal prompts)

# ── ACE-Step music generation ────────────────────────────────
ace_step:
  enabled: false                            # Enable ACE-Step generation
  mode: "lib"                               # lib (local Python) or api (remote server)
  api_url: "http://localhost:8000"          # REST API URL (api mode only)
  model_variant: "turbo"                    # turbo (8 steps, fast) or base (50 steps, quality)
  lm_model_size: "1.7B"                     # 0.6B, 1.7B, or 4B
  use_lm: true                              # "Thinking mode" via language model
  bf16: true                                # bfloat16 precision (false for older GPUs)
  num_versions: 3                           # Versions to generate (1-5)
  hemisphere: "north"                       # north or south
  timeout_seconds: 3600                     # Max time per job (1 hour default)

# ── Content analysis (LLM-based) ────────────────────────────
content_analysis:
  enabled: false                            # Enable LLM content scoring (slower but smarter)
  weight: 0.35                              # Score weight (0-1)
  analyze_frames: 2                         # Frames per segment (1-4)
  min_confidence: 0.5                       # Minimum confidence to use score (0-1)
  frame_max_height: 480                     # Max frame height for LLM (240-1080)
  openai_image_detail: "low"                # low (85 tokens) or high (1889 tokens)

# ── Audio content analysis ───────────────────────────────────
audio_content:
  enabled: false                            # Laughter/speech detection
  weight: 0.15                              # Score weight (0-0.5)
  use_panns: true                           # Use PANNs ML model (requires torch)
  min_confidence: 0.3                       # Detection confidence (0.1-0.9)
  laughter_confidence: 0.2                  # Lower threshold for laughter/baby sounds
  laughter_bonus: 0.1                       # Extra score for laughter segments
  protect_laughter: true                    # Avoid cutting during laughter
  protect_speech: true                      # Avoid cutting during speech

# ── Title screens ────────────────────────────────────────────
title_screens:
  enabled: true                             # Enable title/month/ending screens
  title_duration: 3.5                       # Opening title duration (1-10s)
  month_divider_duration: 2.0               # Month divider duration (1-5s)
  ending_duration: 7.0                      # Ending screen duration (2-15s)
  animation_duration: 0.5                   # Text animation duration (0.2-2s)
  locale: "auto"                            # en, fr, or auto-detect
  style_mode: "auto"                        # auto (mood-based) or random
  animated_background: true                 # Subtle background animations
  show_decorative_lines: true               # Line accents on title screens
  avoid_dark_colors: true                   # Prefer warm light color schemes
  minimum_brightness: 100                   # Min color brightness (0-255)
  show_month_dividers: true                 # Show month divider screens
  month_divider_threshold: 2                # Min clips in a month to show divider
  use_first_name_only: true                 # "Emma" instead of "Emma Smith"
  custom_font_path: null                    # Path to custom TTF/OTF font
```
