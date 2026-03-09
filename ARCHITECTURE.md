# Architecture Guide

> This document is optimized for LLM consumption. Reference it from CLAUDE.md
> to avoid re-reading the full codebase each session.

## Overview

Immich Memories generates video compilations from an Immich photo library.
The pipeline: **fetch clips → analyze → select → assemble → export**.

## Build System

The **Makefile** is the single source of truth for all commands:
- CI (`ci.yml`) uses `make` targets
- Pre-commit hooks use `make` targets for file-length and complexity
- Run `make check` before committing (lint + format + typecheck + file-length + complexity + test)

## Package Structure

```
src/immich_memories/
├── api/                        # Immich server communication
│   ├── immich.py               # SyncImmichClient - REST API wrapper (uses 3 mixins)
│   ├── client_asset.py         # AssetMixin - asset/video operations
│   ├── client_person.py        # PersonMixin - person/face operations
│   ├── client_search.py        # SearchMixin - search operations
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── analysis/                   # Video analysis & clip selection
│   ├── smart_pipeline.py       # SmartPipeline - main orchestrator (uses 4 mixins)
│   ├── pipeline.py             # Pipeline base/helpers
│   ├── pipeline_analysis.py    # AnalysisMixin - clip analysis phase
│   ├── pipeline_preview.py     # PreviewMixin - preview extraction
│   ├── pipeline_refinement.py  # RefinementMixin - clip refinement
│   ├── pipeline_scaling.py     # ScalingMixin - duration scaling
│   ├── progress.py             # Progress tracking helpers
│   ├── clip_selection.py       # Standalone clip selection functions
│   ├── clip_refinement.py      # Standalone refinement functions
│   ├── clip_scaling.py         # Duration scaling helpers
│   ├── unified_analyzer.py     # UnifiedSegmentAnalyzer (uses 2 mixins)
│   ├── segment_scoring.py      # SegmentScoringMixin
│   ├── candidate_generation.py # CandidateGenerationMixin
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
│   └── apple_vision_image.py   # Vision image conversion helpers
│
├── processing/                 # Video processing & assembly
│   ├── assembly.py             # Re-export shim (backwards compat)
│   ├── assembly_config.py      # Dataclasses: AssemblySettings, AssemblyClip, etc.
│   ├── video_assembler.py      # VideoAssembler class (uses 11 mixins)
│   ├── assembler_strategies.py # Assembly strategy methods
│   ├── assembler_encoding.py   # Encoding/trimming methods
│   ├── assembler_transitions.py    # Transition filter building
│   ├── assembler_transition_render.py # Transition rendering
│   ├── assembler_concat.py     # Concatenation/merge methods
│   ├── assembler_audio.py      # Music/audio methods
│   ├── assembler_titles.py     # Title screen integration
│   ├── assembler_helpers.py    # Shared helper methods
│   ├── assembler_scalable.py   # Scalable chunked assembly
│   ├── assembler_batch.py      # Batch merge/direct assembly
│   ├── assembler_probing.py    # FFprobe-based duration probing
│   ├── clips.py                # ClipExtractor - download & re-encode
│   ├── clip_encoding.py        # Clip encoding helpers
│   ├── clip_probing.py         # Clip probing helpers
│   ├── clip_transitions.py     # Clip transition helpers
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
│   └── transition_blend.py     # Frame-level transition blending
│
├── audio/                      # Audio processing
│   ├── content_analyzer.py     # YAMNet audio classification
│   ├── yamnet_analysis.py      # YAMNet analysis helpers
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
│   ├── music_sources.py        # Music source providers (Pixabay, local)
│   └── generators/             # Music generation backends
│       ├── base.py             # Abstract base class
│       ├── factory.py          # Generator factory
│       ├── musicgen_backend.py
│       └── ace_step_backend.py
│
├── titles/                     # Title screen generation
│   ├── generator.py            # TitleScreenGenerator orchestrator
│   ├── rendering_mixin.py      # Rendering mixin for generator
│   ├── ending_mixin.py         # Ending screen mixin
│   ├── convenience.py          # Convenience/factory functions
│   ├── encoding.py             # Title video encoding
│   ├── video_encoding.py       # Video encoding helpers
│   ├── text_builder.py         # Text layout & positioning
│   ├── text_rendering.py       # Text rendering helpers
│   ├── renderer_pil.py         # PIL-based renderer
│   ├── renderer_taichi.py      # Taichi GPU renderer (uses 2 mixins)
│   ├── taichi_kernels.py       # Taichi GPU kernels
│   ├── taichi_particles.py     # Taichi particle systems
│   ├── taichi_text.py          # Taichi text rendering
│   ├── taichi_video.py         # Taichi video creation
│   ├── renderer_ffmpeg.py      # FFmpeg-based renderer
│   ├── backgrounds.py          # Background generation (re-exports)
│   ├── backgrounds_animated.py # Animated gradient backgrounds
│   ├── animations.py           # Text animations
│   ├── styles.py               # Visual style presets
│   ├── colors.py               # Color utilities
│   ├── fonts.py                # Font management
│   ├── sdf_font.py             # SDF font rendering
│   ├── sdf_font_rendering.py   # SDF rendering helpers
│   └── sdf_atlas_gen.py        # SDF atlas generation
│
├── cli/                        # Command-line interface (Click)
│   ├── __init__.py             # Main CLI group + `ui` command
│   ├── generate.py             # `generate`, `analyze`, `export-project`
│   ├── config_cmd.py           # `config`, `people`, `years`, `preflight`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list`, `runs show`, `runs stats`
│   ├── music_cmd.py            # `music search`, `music analyze`
│   ├── hardware_cmd.py         # `hardware` info display
│   └── _helpers.py             # Shared console/print utilities
│
├── ui/                         # NiceGUI web interface
│   ├── app.py                  # App setup & routing
│   ├── state.py                # Shared UI state
│   └── pages/
│       ├── step1_config.py         # Connection & time period config
│       ├── step1_cache.py          # Cache management UI (stats & clear)
│       ├── step2_review.py         # Clip review orchestration
│       ├── step2_loading.py        # Loading state UI
│       ├── step2_helpers.py        # Shared step2 utilities
│       ├── clip_grid.py            # Clip card grid display
│       ├── clip_review.py          # Clip refinement controls
│       ├── clip_pipeline.py        # Pipeline execution UI
│       ├── step3_options.py        # Assembly options
│       ├── step4_export.py         # Export & download
│       └── _step4_music.py         # Music generation/mixing helpers
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
├── cache/                         # Analysis caching system
│   ├── __init__.py                # Re-exports public API
│   ├── database.py                # VideoAnalysisCache class (CRUD/query/stats)
│   ├── database_models.py         # CachedSegment, CachedVideoAnalysis, SimilarVideo
│   ├── database_migrations.py     # DatabaseMigrationsMixin (schema v1-v5)
│   ├── database_queries.py        # DatabaseQueryMixin (read/query methods)
│   ├── thumbnail_cache.py         # ThumbnailCache - file-based thumbnail storage
│   └── video_cache.py             # VideoDownloadCache - downloaded video files
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading logic
├── config_models.py            # Config data models
├── config_models_extra.py      # Additional config models
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization
├── i18n.py                     # Internationalization
└── preflight.py                # Dependency checks
```

## Key Classes & Their Relationships

### Pipeline Flow

```
SmartPipeline.run()
  ├── _phase_cluster()     → ClipExtractor.extract() → Immich API
  ├── _phase_filter()      → quality/resolution/HDR filtering
  ├── _phase_analyze()     → UnifiedSegmentAnalyzer.analyze()
  │                            ├── boundary detection
  │                            ├── candidate generation
  │                            ├── visual + LLM scoring
  │                            └── best segment selection
  └── _phase_refine()      → clip_refinement functions
                               ├── favorites-first selection
                               ├── date distribution
                               └── duration scaling
```

### Assembly Flow

```
VideoAssembler.assemble()
  ├── Strategy selection (cuts / crossfade / smart transitions)
  ├── For each clip:
  │   ├── _build_clip_video_filter() → scale, HDR, rotation
  │   └── _build_audio_prep_filters() → normalize audio
  ├── _build_xfade_chain() → transition filters
  └── _run_ffmpeg_assembly() → FFmpeg execution

VideoAssembler.assemble_with_titles()
  ├── TitleScreenGenerator → title/month/ending screens
  ├── assemble() → main content
  └── _add_music() → background music
```

### Mixin Architecture

**SmartPipeline** inherits from:
- `AnalysisMixin` (pipeline_analysis.py)
- `PreviewMixin` (pipeline_preview.py)
- `RefinementMixin` (pipeline_refinement.py)
- `ScalingMixin` (pipeline_scaling.py)

**UnifiedSegmentAnalyzer** inherits from:
- `SegmentScoringMixin` (segment_scoring.py)
- `CandidateGenerationMixin` (candidate_generation.py)

**VideoAssembler** inherits from (11 mixins):
- `AssemblerHelpersMixin` (assembler_helpers.py)
- `AssemblerProbingMixin` (assembler_probing.py)
- `AssemblerEncodingMixin` (assembler_encoding.py)
- `AssemblerTransitionMixin` (assembler_transitions.py)
- `AssemblerTransitionRenderMixin` (assembler_transition_render.py)
- `AssemblerAudioMixin` (assembler_audio.py)
- `AssemblerConcatMixin` (assembler_concat.py)
- `AssemblerStrategyMixin` (assembler_strategies.py)
- `AssemblerScalableMixin` (assembler_scalable.py)
- `AssemblerBatchMixin` (assembler_batch.py)
- `AssemblerTitleMixin` (assembler_titles.py)

**SyncImmichClient** inherits from:
- `AssetMixin` (client_asset.py)
- `PersonMixin` (client_person.py)
- `SearchMixin` (client_search.py)

**TaichiTitleRenderer** inherits from:
- `TaichiParticlesMixin` (taichi_particles.py)
- `TaichiTextMixin` (taichi_text.py)

## Configuration

- `Config` (config.py) — loaded from `~/.immich-memories/config.yaml`
- `AssemblySettings` (assembly_config.py) — video assembly parameters
- `PipelineConfig` (smart_pipeline.py) — analysis pipeline parameters

## Data Flow

```
Immich API → Asset models → ClipExtractor → VideoClipInfo
  → SmartPipeline → ClipWithSegment (clip + best segment)
  → VideoAssembler → final .mp4
```

## Conventions

- **Max file length**: 500 lines (enforced in CI via `make file-length`)
- **Max complexity**: Xenon grade C (≤20 cyclomatic complexity, `make complexity`)
- **Makefile**: Single source of truth for all commands (CI, pre-commit, CLAUDE.md)
- **Mixins**: Used to split large classes while keeping a single public API
- **Re-export shims**: `assembly.py`, `mixer.py`, `config.py` etc. re-export from sub-modules
- **Private helpers**: Prefixed with `_`, same package
- **Tests**: `tests/` directory, run with `make test`
- **Pre-commit**: Run `make check` before committing
