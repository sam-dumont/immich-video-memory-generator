# Architecture Guide

> This document is optimized for LLM consumption. Reference it from CLAUDE.md
> to avoid re-reading the full codebase each session.

## Overview

Immich Memories generates video compilations from an Immich photo library.
The pipeline: **fetch clips -> analyze -> select -> assemble -> export**.

## Build System

The **Makefile** is the single source of truth for all commands:
- CI (`ci.yml`) uses `make` targets
- Pre-commit hooks use `make` targets for file-length and complexity
- Run `make check` before committing (lint + format + typecheck + file-length + complexity + test)

## Composition Pattern

Large classes compose smaller service objects instead of inheritance: no mixins anywhere.
Each service is a standalone class with a focused responsibility, injected via the constructor.
This keeps classes under the 800-line soft limit (1000 hard) while maintaining a single public API.

The four core orchestrators and their composed services:

**VideoAssembler** (processing/video_assembler.py) composes 6 services:
- `FFmpegProber` (ffmpeg_prober.py): duration/resolution probing via ffprobe
- `FilterBuilder` (filter_builder.py): FFmpeg filter graph construction
- `ClipEncoder` (clip_encoder.py): per-clip trimming and re-encoding
- `AssemblyEngine` (assembly_engine.py): strategy-based multi-clip assembly
  - internally composes `ConcatService` (_assembly_concat.py)
- `AudioMixerService` (audio_mixer_service.py): background music mixing
- `TitleInserter` (title_inserter.py): title screen concatenation

**SmartPipeline** (analysis/smart_pipeline.py) composes 4 services:
- `ClipAnalyzer` (clip_analyzer.py): download, analyze, and score clips
- `PreviewBuilder` (preview_builder.py): extract preview segments
- `ClipRefiner` (clip_refiner.py): select and distribute final clips
- `ClipScaler` (clip_scaler.py): scale to target duration, deduplicate

**ImmichClient** (api/immich.py) composes 5 services:
- `SearchService` (search_service.py): video search and time bucket queries
- `AllAssetsService` (all_assets_service.py): type-agnostic asset queries (trip detection)
- `AssetService` (asset_service.py): asset/video download operations
- `PersonService` (person_service.py): person/face operations
- `AlbumService` (album_service.py): album operations

**TitleScreenGenerator** (titles/generator.py) composes 3 services:
- `RenderingService` (rendering_service.py): GPU/CPU renderer selection, video creation
- `EndingService` (ending_service.py): fade-to-white ending generation
- `TripService` (trip_service.py): trip map and location card screens

Resolution/context setup lives in standalone functions:
- `assembly_context_builder.py`: `resolve_target_resolution()`, `create_assembly_context()`

not a top-level orchestrator.

## Package Structure

```
src/immich_memories/
├── api/                        # Immich server communication
│   ├── immich.py               # ImmichClient (composes 5 services)
│   ├── search_service.py       # SearchService: video search, time buckets
│   ├── all_assets_service.py   # AllAssetsService: type-agnostic queries
│   ├── asset_service.py        # AssetService: asset/video download
│   ├── person_service.py       # PersonService: person/face operations
│   ├── album_service.py        # AlbumService: album operations
│   ├── sync_client.py          # Sync wrapper for async client
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── memory_types/               # Memory type presets & factory
│   ├── __init__.py             # Public API re-exports
│   ├── registry.py             # MemoryType enum
│   ├── presets.py              # ScoringProfile, PersonFilter, MemoryPreset
│   ├── date_builders.py        # build_season(), build_month(), build_on_this_day()
│   └── factory.py              # Registry + 6 built-in preset factories
│
├── analysis/                   # Video analysis & clip selection
│   ├── smart_pipeline.py       # SmartPipeline (composes 4 services)
│   ├── pipeline.py             # Pipeline base/helpers
│   ├── clip_analyzer.py        # ClipAnalyzer: download + analyze + score
│   ├── clip_refiner.py         # ClipRefiner: final selection + distribution
│   ├── clip_scaler.py          # ClipScaler: duration scaling + dedup
│   ├── clip_refinement.py      # Standalone refinement functions
│   ├── clip_scaling.py         # Duration scaling helpers
│   ├── clip_selection.py       # Standalone clip selection functions
│   ├── preview_builder.py      # PreviewBuilder: preview segment extraction
│   ├── progress.py             # Progress tracking helpers
│   ├── trip_detection.py       # GPS-based trip detection (clustering, geocoding)
│   ├── trip_scoring.py         # Location diversity scoring for trip clips
│   ├── unified_analyzer.py     # UnifiedSegmentAnalyzer (all methods merged, no mixins)
│   ├── content_analyzer.py     # LLM-based content analysis
│   ├── _content_parsing.py     # Content analysis response parsing
│   ├── _content_providers.py   # Content analysis provider helpers
│   ├── analyzer_factory.py     # Analyzer factory
│   ├── analyzer_models.py      # Analyzer data models
│   ├── duplicates.py           # Duplicate/near-duplicate detection
│   ├── duplicate_hashing.py    # Perceptual hashing for duplicates
│   ├── thumbnail_clustering.py # Thumbnail-based clustering
│   ├── scoring.py              # Quality scoring (delegates to sub-modules)
│   ├── scoring_face.py         # Face detection scoring
│   ├── scoring_motion.py       # Motion/stability scoring
│   ├── scoring_segments.py     # Segment generation helpers
│   ├── scoring_factory.py      # Scorer factory & sampling
│   ├── scenes.py               # Scene detection
│   ├── silence_detection.py    # Audio silence detection
│   ├── apple_vision.py         # macOS Vision framework integration
│   ├── apple_vision_image.py   # Vision image conversion helpers
│   └── llm_query.py            # LLM query helpers
│
├── processing/                 # Video processing & assembly
│   ├── video_assembler.py      # VideoAssembler (composes 7 services)
│   ├── assembly_engine.py      # AssemblyEngine (composes ConcatService)
│   ├── _assembly_concat.py     # ConcatService: concat/xfade/batch ops
│   ├── assembly_config.py      # Dataclasses: AssemblySettings, AssemblyClip, etc.
│   ├── assembly_context_builder.py # Standalone: resolve_target_resolution(), create_assembly_context()
│   ├── ffmpeg_prober.py        # FFmpegProber: ffprobe-based duration/resolution
│   ├── filter_builder.py       # FilterBuilder: FFmpeg filter graph construction
│   ├── clip_encoder.py         # ClipEncoder: per-clip trimming/re-encoding
│   ├── clip_encoding.py        # Clip encoding helpers
│   ├── clip_probing.py         # Clip probing helpers
│   ├── clip_transitions.py     # Clip transition helpers
│   ├── clips.py                # ClipExtractor: download & re-encode
│   ├── title_inserter.py       # TitleInserter: title screen concatenation
│   ├── audio_mixer_service.py  # AudioMixerService: background music mixing
│   ├── downscaler.py           # Resolution downscaling
│   ├── hdr_utilities.py        # HDR detection & conversion filters
│   ├── scaling_utilities.py    # Resolution, aspect ratio, smart crop
│   ├── ffmpeg_runner.py        # FFmpeg execution with progress
│   ├── hardware.py             # Hardware detection (GPU, encoders)
│   ├── hardware_detection.py   # Hardware detection backends
│   ├── _hardware_backends.py   # GPU backend detection
│   ├── transforms.py           # Video transforms (rotate, scale)
│   ├── _transforms_ffmpeg.py   # FFmpeg transform filters
│   ├── _transforms_smart_crop.py # Smart crop transforms
│   └── live_photo_merger.py    # Live Photo merging
│
├── audio/                      # Audio processing
│   ├── content_analyzer.py     # PANNs audio classification
│   ├── panns_analysis.py       # PANNs (PyTorch AudioSet) helpers
│   ├── energy_analysis.py      # Audio energy analysis
│   ├── audio_models.py         # Audio data models
│   ├── mixer.py                # Audio mixing & ducking (re-exports)
│   ├── mixer_class.py          # AudioMixer class
│   ├── mixer_helpers.py        # Mixing helper functions
│   ├── mood_analyzer.py        # Mood detection for music matching
│   ├── mood_analyzer_backends.py # Mood analysis backends
│   ├── music_generator.py      # AI music generation orchestrator
│   ├── music_generator_client.py # Music generation client
│   ├── music_generator_models.py # Music generation data models
│   ├── music_sources.py        # Music source providers (local library)
│   ├── music_pipeline.py       # Multi-provider pipeline (ACE-Step -> MusicGen fallback)
│   └── generators/             # Music generation backends
│       ├── base.py             # Abstract MusicGenerator interface
│       ├── factory.py          # Generator factory
│       ├── musicgen_backend.py # MusicGen API (generation + Demucs stems)
│       ├── ace_step_backend.py # ACE-Step REST API (generation)
│       └── ace_step_captions.py # Dense caption templates
│
├── titles/                     # Title screen generation
│   ├── generator.py            # TitleScreenGenerator (composes 3 services)
│   ├── rendering_service.py    # RenderingService: GPU/CPU renderer selection
│   ├── ending_service.py       # EndingService: fade-to-white ending
│   ├── trip_service.py         # TripService: trip map + location cards
│   ├── _text_memory_types.py   # Memory type title helpers
│   ├── _trip_titles.py         # Trip title text generation
│   ├── convenience.py          # Convenience/factory functions
│   ├── encoding.py             # Title video encoding
│   ├── video_encoding.py       # Video encoding helpers
│   ├── text_builder.py         # Text layout & positioning
│   ├── text_rendering.py       # Text rendering helpers
│   ├── renderer_pil.py         # PIL-based renderer
│   ├── renderer_taichi.py      # Taichi GPU renderer
│   ├── renderer_ffmpeg.py      # FFmpeg-based renderer
│   ├── taichi_kernels.py       # Taichi GPU kernels
│   ├── taichi_particles.py     # Taichi particle systems
│   ├── taichi_text.py          # Taichi text rendering
│   ├── taichi_video.py         # Taichi video creation
│   ├── taichi_globe.py         # Taichi globe rendering
│   ├── globe_renderer.py       # Globe renderer
│   ├── globe_video.py          # Globe video creation
│   ├── map_animation.py        # Map animation
│   ├── map_renderer.py         # Map tile rendering (staticmap + PIL overlay)
│   ├── backgrounds.py          # Background generation (re-exports)
│   ├── backgrounds_animated.py # Animated gradient backgrounds
│   ├── animations.py           # Text animations
│   ├── styles.py               # Visual style presets
│   ├── colors.py               # Color utilities
│   ├── fonts.py                # Font management
│   ├── llm_titles.py           # LLM-generated titles
│   ├── sdf_font.py             # SDF font rendering
│   ├── sdf_font_rendering.py   # SDF rendering helpers
│   └── sdf_atlas_gen.py        # SDF atlas generation
│
├── cli/                        # Command-line interface (Click)
│   ├── __init__.py             # Main CLI group + `ui` command
│   ├── generate.py             # `generate`, `analyze`, `export-project`
│   ├── config_cmd.py           # `config`, `people`, `years`, `preflight`
│   ├── scheduler_cmd.py        # `scheduler start/stop/status/list`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list`, `runs show`, `runs stats`
│   ├── music_cmd.py            # `music search`, `music analyze`
│   ├── hardware_cmd.py         # `hardware` info display
│   ├── _helpers.py             # Shared console/print utilities
│   ├── _analyze_export.py      # Analysis export helpers
│   ├── _config_errors.py       # Config error formatting
│   ├── _trip_display.py        # Trip detection display & selection
│   └── _date_resolution.py     # Date range resolution for memory types
│
├── ui/                         # NiceGUI web interface
│   ├── app.py                  # App setup & routing
│   ├── state.py                # Shared UI state
│   ├── theme.py                # UI theme
│   ├── components.py           # Shared UI components
│   ├── filename_builder.py     # Output filename generation
│   └── pages/
│       ├── step1_config.py         # Connection & time period config
│       ├── step1_cache.py          # Cache management UI
│       ├── step1_presets.py        # Memory preset selection
│       ├── step1_tabs.py           # Step 1 tab layout
│       ├── step2_review.py         # Clip review orchestration
│       ├── step2_loading.py        # Loading state UI
│       ├── step2_helpers.py        # Shared step2 utilities
│       ├── _step2_live_photos.py   # Live photo handling
│       ├── clip_grid.py            # Clip card grid display
│       ├── clip_review.py          # Clip refinement controls
│       ├── clip_pipeline.py        # Pipeline execution UI
│       ├── clip_pipeline_helpers.py # Pipeline helper functions
│       ├── pipeline_title.py       # Pipeline title display
│       ├── step3_options.py        # Assembly options
│       ├── _step3_music_preview.py # Music preview controls
│       ├── step4_export.py         # Export & download
│       ├── _step4_generate.py      # Generation logic
│       ├── _step4_upload.py        # Upload-back to Immich
│       ├── _step4_music.py         # Music generation/mixing helpers
│       └── settings_config.py      # Settings page
│
├── tracking/                   # Run history & telemetry
│   ├── run_database.py         # SQLite run storage
│   ├── run_queries.py          # Database query helpers
│   ├── run_tracker.py          # Pipeline run tracking
│   ├── run_id.py               # Run ID generation
│   ├── active_jobs_mixin.py    # Active jobs tracking mixin
│   ├── models.py               # Run/phase data models
│   └── system_info.py          # System info collection
│
├── cache/                      # Analysis caching system
│   ├── __init__.py             # Re-exports public API
│   ├── database.py             # VideoAnalysisCache class
│   ├── database_models.py      # CachedSegment, CachedVideoAnalysis, SimilarVideo
│   ├── database_migrations.py  # Schema migrations (v1-v5)
│   ├── database_queries.py     # Read/query methods
│   ├── thumbnail_cache.py      # File-based thumbnail storage
│   └── video_cache.py          # Downloaded video file cache
│
├── scheduling/                 # Scheduled memory generation
│   ├── engine.py               # Scheduler: cron parsing, next job calculation
│   ├── executor.py             # JobExecutor: parameter resolution
│   ├── daemon.py               # Daemon loop (foreground, SIGINT/SIGTERM)
│   └── models.py               # Scheduling data models
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading logic
├── config_models.py            # Config data models
├── config_models_extra.py      # Additional config models
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization
├── i18n.py                     # Internationalization
├── preflight.py                # Dependency checks
└── logging_config.py           # Logging setup
```

## Key Classes & Their Relationships

### Pipeline Flow

```
SmartPipeline.run()
  ├── _phase_cluster()     → ClipExtractor.extract() → Immich API
  ├── _phase_filter()      → quality/resolution/HDR filtering
  ├── _phase_analyze()     → ClipAnalyzer.analyze()
  │                            via UnifiedSegmentAnalyzer:
  │                            ├── boundary detection
  │                            ├── candidate generation
  │                            ├── visual + LLM scoring
  │                            └── best segment selection
  └── _phase_refine()      → ClipRefiner.refine()
                               ├── favorites-first selection
                               ├── date distribution
                               └── ClipScaler: duration scaling
```

### Assembly Flow

```
VideoAssembler.assemble()
  ├── AssemblyEngine picks strategy (cuts / crossfade / smart transitions)
  ├── For each clip:
  │   ├── FilterBuilder.build_clip_video_filter() → scale, HDR, rotation
  │   └── FilterBuilder.build_audio_prep_filters() → normalize audio
  └── AssemblyEngine → ConcatService → FFmpeg execution

VideoAssembler.assemble_with_titles()
  ├── TitleScreenGenerator → title/month/ending screens
  ├── assemble() → main content
  └── AudioMixerService → background music
```

## Configuration

- `Config` (config.py): loaded from `~/.immich-memories/config.yaml`
- `AssemblySettings` (assembly_config.py): video assembly parameters
- `PipelineConfig` (smart_pipeline.py): analysis pipeline parameters

## Data Flow

```
Immich API → Asset models → ClipExtractor → VideoClipInfo
  → SmartPipeline → ClipWithSegment (clip + best segment)
  → VideoAssembler → final .mp4
```

## Conventions

- **Max file length**: 500 lines (enforced in CI via `make file-length`)
- **Max complexity**: Xenon grade C (<=20 cyclomatic complexity, `make complexity`)
- **Makefile**: Single source of truth for all commands (CI, pre-commit, CLAUDE.md)
- **Composition**: Top-level orchestrators compose service objects via constructor injection
- **Re-export shims**: `mixer.py`, `config.py` etc. re-export from sub-modules for backwards compat
- **Private helpers**: Prefixed with `_`, same package
- **Tests**: `tests/` directory, run with `make test`
- **Pre-commit**: Run `make check` before committing
