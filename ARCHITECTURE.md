# Architecture Guide

> This document is optimized for LLM consumption. Reference it from CLAUDE.md
> to avoid re-reading the full codebase each session.

## Overview

Immich Memories generates video compilations from an Immich photo library.
The public lifecycle of one run (`operations/phases.py`, `OperationalPhase`):
**discovery -> download -> analysis -> selection -> render -> music -> delivery**.
Inside the analysis step, `SmartPipeline` reports its own sub-phases
(`analysis/progress.py`, `PipelinePhase`): **clustering -> filtering -> analyzing -> refining**.

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
  - internally composes `ConcatService` (ffmpeg_filter_graph.py)
- `AudioMixerService` (audio_mixer_service.py): background music mixing
- `TitleInserter` (title_inserter.py): title screen concatenation
  - composes `TitleBackgroundRenderer` (title_background_renderer.py) for the pre-rendered
    clip a title deblurs out of, and builds one `TitleDividerPlanner`
    (title_divider_planner.py) per memory to place month/year/location cards

**SmartPipeline** (analysis/smart_pipeline.py) composes 5 services:
- `ClipAnalyzer` (clip_analyzer.py): download, analyze, and score clips
- `PreviewBuilder` (preview_builder.py): extract preview segments
- `ClipRefiner` (clip_refiner.py): select and distribute final clips
- `ClipScaler` (clip_scaler.py): scale to target duration, deduplicate
- `SelectionQuality` (selection_quality.py): verify, judge and review the finished cut

It also owns a `ProviderCircuit` (provider_health.py, LLM provider circuit breaker) and an
optional `VideoDownloadCache`. The density-proportional asset budget is a plain function,
`compute_density_budget()` (density_budget.py), called from `_phase_filter()` — not an
injected service.

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

**`assemble_streaming()`** (processing/streaming_assembler.py) is the streaming render path,
a three-stage pipe rather than a class: `make_decoder()` (streaming_frame_decoder.py) turns a
clip into normalized raw frames, `FrameBlender` (streaming_frame_blender.py) writes those
frames to a `FrameSink` and crossfades across clip boundaries, and `StreamingEncoder` (the
sink it is constructed with) pipes them into FFmpeg.

**TaichiTitleRenderer** (titles/renderer_taichi.py) owns the background and the per-frame
GPU pipeline, and composes 2 services (both take Protocol-typed config/buffers):
- `ParticleField` (taichi_particles.py): bokeh drift and fireworks physics, CPU numpy only
- `TitleTextRenderer` (taichi_text.py): SDF and PIL text compositing onto the frame buffer

**`generate_memory()`** (generate.py) is the top-level orchestrator above the four; it runs
the `OperationalPhase` lifecycle end to end (there is no `GenerationPipeline` class) with
these helper modules:
- `generate_downloads.py`: parallel asset downloads
- `generate_clips.py`: clip extraction, probing, cleanup
- `generate_photos.py`: photo rendering, budget allocation, clip merging
- `generate_music.py`: music resolution, AI generation, audio mixing
- `generate_privacy.py`: GPS anonymization, fake names/cities, trip titles
- `generate_settings.py`: assembly/title settings, assembler creation
- `generate_timeline.py`: final-duration validation and content budget guards

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
│   ├── compatibility.py        # Immich API-version compatibility policy (v1/v2 resolution)
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── photos/                     # Photo-to-video animation (converts stills to .mp4 clips)
│   ├── __init__.py             # Public API re-exports
│   ├── renderer.py             # Frame-by-frame renderer: Ken Burns, face_aware_pan, render_split (parked)
│   ├── animator.py             # Photo source prep: HEIC decode, downscale cap, HDR detection
│   ├── photo_pipeline.py       # PhotoPipeline: end-to-end photo processing orchestrator
│   ├── ultrahdr.py             # Ultra HDR JPEG (Android/Pixel): MPF parser, gain map, ISO 21496-1
│   └── scoring.py              # Photo scoring: favorites, faces, camera, penalty
│
├── memory_types/               # Memory type presets & factory
│   ├── __init__.py             # Public API re-exports
│   ├── registry.py             # MemoryType enum
│   ├── presets.py              # ScoringProfile, PersonFilter, MemoryPreset
│   ├── date_builders.py        # build_season(), build_month(), build_on_this_day()
│   └── factory.py              # Registry + 7 built-in preset factories (incl. trip)
│
├── analysis/                   # Video analysis & clip selection
│   ├── smart_pipeline.py       # SmartPipeline (composes 5 services)
│   ├── pipeline.py             # ClusterManager / DuplicateCluster: duplicate cluster bookkeeping
│   ├── provider_health.py      # ProviderCircuit: bounded, credential-safe LLM provider health
│   ├── cache_projection.py     # Project compatible cached analysis back onto in-memory clips
│   ├── clip_analyzer.py        # ClipAnalyzer: download + analyze + score
│   ├── clip_refiner.py         # ClipRefiner: final selection + distribution
│   ├── clip_scaler.py          # ClipScaler: duration scaling + dedup
│   ├── selection_quality.py    # SelectionQuality: verify + judge + review
│   ├── clip_selection.py       # Standalone clip selection functions
│   ├── density_budget.py       # compute_density_budget(): density-proportional asset budget
│   ├── preview_builder.py      # PreviewBuilder: preview segment extraction
│   ├── progress.py             # Progress tracking helpers
│   ├── trip_detection.py       # GPS-based trip detection (clustering, geocoding)
│   ├── unified_analyzer.py     # UnifiedSegmentAnalyzer (composes SpeechAnalysisService)
│   ├── speech_analysis.py      # SpeechAnalysisService: PANNs audio-content + VAD speech boundaries
│   ├── segment_transcription.py # Transcribe the top candidate segments (whisper via speech/transcription.py)
│   ├── unified_budget.py       # Unified photo+video budget selection (merge-then-fit)
│   ├── segment_generation.py   # Boundary detection, candidate segment generation
│   ├── boundary_placement.py   # Where a cut may land: protected-range gaps, edge selection
│   ├── content_analyzer.py     # LLM-based content analysis
│   ├── llm_response_parser.py  # Content analysis response parsing
│   ├── _content_providers.py   # Ollama / OpenAI-compatible ContentAnalyzer implementations
│   ├── request_heartbeat.py    # RequestHeartbeat: periodic log line for long-outstanding HTTP calls
│   ├── analyzer_factory.py     # Analyzer factory
│   ├── analyzer_models.py      # Analyzer data models
│   ├── duplicates.py           # Duplicate/near-duplicate detection
│   ├── duplicate_hashing.py    # Perceptual hashing for duplicates
│   ├── thumbnail_clustering.py # Thumbnail-based clustering
│   ├── thumbnail_prefetch.py   # ThumbnailPrefetcher: fills the thumbnail cache before phase 1 (CLI/auto path)
│   ├── scoring.py              # Quality scoring (motion, duration, segments) + SceneScorer
│   ├── face_scoring.py         # Face detection scoring: Apple Vision / OpenCV backends
│   ├── scenes.py               # Scene detection
│   ├── silence_detection.py    # Audio silence detection
│   ├── apple_vision.py         # macOS Vision framework integration
│   ├── apple_vision_image.py   # Vision image conversion helpers
│   ├── llm_query.py            # LLM query helpers
│   └── live_photo_pipeline.py  # Live Photo fetch, cluster, convert (shared CLI/UI)
│
├── processing/                 # Video processing & assembly
│   ├── video_assembler.py      # VideoAssembler (composes 6 services)
│   ├── assembly_engine.py      # AssemblyEngine (composes ConcatService)
│   ├── ffmpeg_filter_graph.py  # ConcatService: batch merge/direct assembly
│   ├── assembly_config.py      # Dataclasses: AssemblySettings, AssemblyClip, etc.
│   ├── streaming_assembler.py  # StreamingEncoder + assemble_streaming(): low-memory 4K assembly
│   ├── streaming_frame_decoder.py # FrameDecoder / make_decoder(): clip -> normalized raw frames
│   ├── streaming_frame_blender.py # FrameBlender: frames -> sink, crossfades, progress/preview
│   ├── streaming_audio.py      # Streaming audio processing helpers
│   ├── ffmpeg_prober.py        # FFmpegProber: ffprobe-based duration/resolution
│   ├── filter_builder.py       # FilterBuilder: FFmpeg filter graph construction
│   ├── clip_encoder.py         # ClipEncoder: per-clip trimming/re-encoding
│   ├── clip_probing.py         # Clip probing helpers
│   ├── clip_transitions.py     # Clip transition helpers
│   ├── clip_validation.py      # Clip validation helpers
│   ├── clips.py                # ClipExtractor: download & re-encode
│   ├── download_coordinator.py # DownloadCoordinator: bounded prefetching, one sync client per worker
│   ├── probe_cache.py          # ProbeCache: run-scoped normalized source probing (injected into FFmpegProber)
│   ├── encoding_plan.py        # EncodingPlan / resolve_encoding_plan(): immutable output encoding contract
│   ├── output_canvas.py        # Resolve the single pixel canvas used by one run
│   ├── output_contract.py      # probe/validate/atomically publish finished video artifacts
│   ├── timeline_budget.py      # plan_timeline(): pure planning of content + title-screen timeline
│   ├── title_inserter.py       # TitleInserter: title screen concatenation
│   ├── title_background_renderer.py # TitleBackgroundRenderer: pre-renders the clip a title reveals into
│   ├── title_divider_planner.py # TitleDividerPlanner: month/year/location divider cards
│   ├── audio_mixer_service.py  # AudioMixerService: background music mixing
│   ├── privacy_audio.py        # Privacy mode audio processing (lowpass filter)
│   ├── frame_preview.py        # Frame extraction for previews
│   ├── downscaler.py           # Resolution downscaling
│   ├── hdr_utilities.py        # HDR detection & conversion filters
│   ├── scaling_utilities.py    # Resolution, aspect ratio, smart crop
│   ├── ffmpeg_runner.py        # FFmpeg execution with progress
│   ├── hardware.py             # Hardware detection (GPU, encoders)
│   ├── hardware_detection.py   # Hardware detection backends
│   ├── transforms.py           # Video transforms (rotate, scale)
│   ├── transforms_ffmpeg.py    # FFmpeg transform filters
│   ├── transforms_smart_crop.py # Smart crop transforms
│   └── live_photo_merger.py    # Live Photo merging
│
├── audio/                      # Audio processing
│   ├── content_analyzer.py     # PANNs audio classification
│   ├── audio_models.py         # Audio data models
│   ├── mixer.py                # Audio mixing & ducking
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
│       ├── base.py             # MusicGenerator ABC + StemSeparator Protocol
│       ├── factory.py          # Generator factory
│       ├── musicgen_backend.py # MusicGen API (generation + remote Demucs stems)
│       ├── ace_step_backend.py # ACE-Step lib/API (generation)
│       ├── ace_step_captions.py # Dense caption templates
│       └── demucs_local.py     # Local Demucs stem separation (in-process)
│
├── speech/                     # Voice activity for clip boundaries (optional `speech` extra)
│   ├── vad.py                  # extract_audio_16k, silence_gaps, select_detector
│   ├── fireredvad.py           # FireRedSpeechDetector: vendored AED ONNX + Kaldi fbank/CMVN
│   ├── boundary_scoring.py     # BoundaryWeights, candidates_from_gaps, best_boundary
│   ├── turn_detection.py       # SmartTurnDetector (weighted 0.0; deps in no extra)
│   ├── transcription.py        # whisper.cpp transcription of one segment's audio slice (`transcribe` extra)
│   ├── models.py               # SpeechRegion, BoundaryCandidate
│   └── bundled_models/         # fireredvad_aed.onnx (2.4 MB, Apache-2.0) — no runtime download
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
│   ├── content_background.py   # Content-aware background generation
│   ├── renderer_pil.py         # PIL-based renderer
│   ├── renderer_taichi.py      # TaichiTitleRenderer: background + frame pipeline
│   ├── taichi_particles.py     # ParticleField: bokeh drift / fireworks physics
│   ├── taichi_text.py          # TitleTextRenderer: SDF + PIL text compositing
│   ├── renderer_ffmpeg.py      # FFmpeg-based renderer
│   ├── taichi_kernels.py       # Taichi GPU kernels
│   ├── taichi_video.py         # Taichi video creation
│   ├── map_animation.py        # Satellite map fly-over (van Wijk zoom)
│   ├── map_renderer.py         # Map tile rendering (staticmap + PIL overlay)
│   ├── backgrounds.py          # Background generation
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
│   ├── generate.py             # `generate`
│   ├── _analyze_export.py      # `analyze`, `export-project`
│   ├── config_cmd.py           # `config`, `people`, `years`, `preflight`
│   ├── scheduler_cmd.py        # `scheduler list/status/start`
│   ├── auto_cmd.py             # `auto suggest/run/history/status/install/test-notification`
│   ├── cache_cmd.py            # `cache stats/export/import/backup`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list/show/stats/storage/delete`
│   ├── music_cmd.py            # `music search/analyze/add`
│   ├── hardware_cmd.py         # `hardware` info display
│   ├── _helpers.py             # Shared console/print utilities
│   ├── _generation_preview.py  # Plain-text summary for read-only generation planning (--dry-run)
│   ├── _config_errors.py       # Config error formatting
│   ├── _pipeline_runner.py     # Fetch assets + run SmartPipeline + generate
│   ├── _trip_generation.py     # Trip detection, selection, per-trip generation
│   ├── _trip_display.py        # Trip table formatting & selection logic
│   ├── _date_resolution.py     # Date range resolution for memory types
│   ├── _generate_display.py    # Params table + result printing for `generate`
│   ├── _live_display.py        # Rich Live interactive progress display
│   └── _progress.py            # Progress tracking helpers
│
├── ui/                         # NiceGUI web interface
│   ├── app.py                  # App setup & routing
│   ├── auth.py                 # Auth middleware, credential verification, session helpers
│   ├── auth_oidc.py            # OIDC client (authlib starlette integration, singleton)
│   ├── reverse_proxy.py        # Secure cookie + trusted X-Forwarded-* kwargs for ui.run
│   ├── state.py                # Shared UI state
│   ├── theme.py                # UI theme
│   ├── components.py           # Shared UI components
│   ├── nicegui_compat.py       # Compatibility helpers for NiceGUI background work
│   └── pages/
│       ├── login.py                # Login page (basic form + OIDC SSO button)
│       ├── step1_config.py         # Connection & time period config
│       ├── step1_cache.py          # Cache management UI
│       ├── step1_presets.py        # Memory preset selection
│       ├── step1_tabs.py           # Step 1 tab layout
│       ├── step2_review.py         # Clip review orchestration
│       ├── step2_loading.py        # Loading state UI
│       ├── step2_helpers.py        # Shared step2 utilities
│       ├── clip_grid.py            # Clip card grid display
│       ├── clip_review.py          # Clip refinement controls
│       ├── clip_pipeline.py        # Pipeline execution UI
│       ├── clip_pipeline_helpers.py # Pipeline helper functions
│       ├── pipeline_title.py       # Pipeline title display
│       ├── step3_options.py        # Assembly options
│       ├── _step3_music_preview.py # Music preview controls
│       ├── step4_export.py         # Export & download
│       ├── _step4_generate.py      # Generation logic
│       ├── step4_recovery.py       # Reload recovers a run that outlived the page
│       ├── _step4_upload.py        # Upload-back to Immich
│       ├── _step4_music.py         # Music generation/mixing helpers
│       └── settings_config.py      # Settings page
│
├── tracking/                   # Run history & telemetry
│   ├── run_database.py         # SQLite run storage
│   ├── run_tracker.py          # Pipeline run tracking
│   ├── run_id.py               # Run ID generation
│   ├── models.py               # Run/phase data models
│   └── system_info.py          # System info collection
│
├── cache/                      # Analysis caching system
│   ├── __init__.py             # Re-exports public API
│   ├── database.py             # VideoAnalysisCache class (SQLite reads/writes)
│   ├── schema_migrator.py      # SchemaMigrator: schema ladder v1..vN, DDL
│   ├── database_models.py      # CachedSegment, CachedVideoAnalysis, SimilarVideo
│   ├── database_rows.py        # SQLite row <-> model conversion
│   ├── versions.py             # SCHEMA_VERSION / ANALYSIS_VERSION (independent)
│   ├── migration_sql.py        # Transactional migration helpers
│   ├── migration_v11.py … v17.py # One module per schema migration
│   ├── asset_score_cache.py    # Asset score persistence (photo/video scores)
│   ├── thumbnail_cache.py      # File-based thumbnail storage
│   └── video_cache.py          # Downloaded video file cache
│
├── scheduling/                 # Scheduled memory generation
│   ├── engine.py               # Scheduler: cron parsing, next job calculation
│   ├── executor.py             # resolve_schedule_params(): schedule entry -> generation params
│   ├── daemon.py               # Daemon loop (foreground, SIGINT/SIGTERM)
│   └── models.py               # Scheduling data models
│
├── automation/                 # Smart automation (auto suggest/run)
│   ├── __init__.py             # Public API re-exports
│   ├── candidates.py           # Memory candidate detection
│   ├── candidate_scorer.py     # Candidate scoring & ranking
│   ├── candidate_discovery.py  # CandidateDiscovery: one library snapshot -> ranked candidates
│   ├── event_detectors.py      # Event-based detectors (activity bursts)
│   ├── calendar_detectors.py   # Calendar-based detectors (monthly, yearly)
│   ├── variety.py              # Cadence and rotation rules for candidates
│   ├── models.py               # Typed values returned/persisted by automation
│   ├── generation_request.py   # Typed boundary from candidates to the `generate` CLI
│   ├── state_store.py          # SQLite persistence for automation attempts
│   ├── status.py               # Cooldown gate + read-only AutomationStatus contract
│   ├── delivery_retry.py       # Durable state for one pending delivery retry
│   ├── notification_state.py   # Durable, sanitized notification delivery health
│   ├── trip_input_cache.py     # Durable, identity-checked inputs for auto trip discovery
│   ├── notifications.py        # Apprise notification integration
│   ├── runner.py               # Auto-run orchestrator (lease, subprocess, attempt record)
│   ├── in_process_scheduler.py # Daily timer inside the UI/Docker process
│   └── system_scheduler.py     # OS scheduler integration (launchd/systemd/cron)
│
├── operations/                 # Public lifecycle contract + read-only ops reports
│   ├── phases.py               # OperationalPhase / PhaseEvent: stable outer lifecycle
│   └── storage_report.py       # build_storage_report(): output + cache storage inventory (`runs storage`)
│
├── planning/                   # Media-aware duration planning
│   └── auto_duration.py        # resolve_trip_auto_duration(): trip auto-duration heuristics
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading logic
├── config_models.py            # Config data models
├── config_models_auth.py       # Authentication config model (basic, OIDC, header)
├── config_models_server.py     # UI server bind settings + secure-by-default host rule
├── generate.py                 # End-to-end generation orchestrator
├── generate_clips.py           # Clip extraction, probing, cleanup
├── generate_downloads.py       # Parallel asset downloads
├── generate_music.py           # Music resolution, AI generation, audio mixing
├── generate_photos.py          # Photo rendering, budget allocation, clip merging
├── generate_privacy.py         # GPS anonymization, fake names/cities, trip titles
├── generate_settings.py        # Assembly/title settings, assembler creation, music, upload
├── generate_timeline.py        # Final-duration validation + content budget guards
├── filename_builder.py         # Output filename generation
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization
├── i18n.py                     # Internationalization
├── preflight.py                # Dependency checks
├── logging_config.py           # Logging setup
└── _version.py                 # Auto-generated by hatch-vcs (do not edit)
```

## Key Classes & Their Relationships

### Pipeline Flow (Unified Selection)

Videos and photos compete in a single selection pool:

```
SmartPipeline.run_analysis()           (Phases 1-3: videos only)
  ├── _phase_cluster()            → thumbnail dedup → Immich API
  ├── _hard_eligible_clips()      → hard eligibility filter
  ├── _analysis_candidates()      → analysis depth (fast/auto/thorough) → shortlist
  │     └── _phase_filter()       → compute_density_budget(), _adapt_target_for_content()
  └── _analyze_with_cache_batch() → ClipAnalyzer.analyze() (one cache batch)
        │  (leftovers: ClipAnalyzer.plan_cached_or_metadata() fallback)
        └── via UnifiedSegmentAnalyzer:
              ├── boundary detection
              ├── candidate generation
              ├── protected-range adjustment
              │     speech/ VAD regions ∪ non-speech PANNs events
              ├── visual + LLM scoring
              ├── transcription of top segments (segment_transcription.py, optional)
              └── best segment selection

score_photos()                         (Photos: metadata + LLM thumbnails)
  ├── metadata scoring (favorites, faces, camera)
  └── LLM enhancement on shortlist

MERGE → all candidates as ClipWithSegment

SmartPipeline.run_selection()          (Phase 4: unified pool)
  └── ClipRefiner.phase_refine()
       ├── favorites-first selection
       ├── temporal coverage (1 clip per month/week guaranteed)
       ├── ClipScaler: duration scaling (sole reps protected)
       ├── temporal dedup (photos + videos together)
       └── type interleaving (max 2 consecutive same type)
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

- `Config` (config_loader.py): loaded from `~/.immich-memories/config.yaml`, tiered YAML (see above)
- `AssemblySettings` (assembly_config.py): video assembly parameters
- `PipelineConfig` (smart_pipeline.py): analysis pipeline parameters

## Data Flow

```
Immich API → Asset models → ClipExtractor → VideoClipInfo
  → SmartPipeline → ClipWithSegment (clip + best segment)
  → VideoAssembler → final .mp4
```

## Configuration Tiers

Config is organized in 3 tiers (see `config_loader.py`):

- **Tier 1** (top-level YAML): `immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`, `trips`, `photos`
- **Tier 2** (under `advanced:` in YAML, `_TIER2_SECTIONS`): `analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `content_analysis`, `audio_content`, `speech`, `transcription`, `server`, `auth`, `automation`, `notifications`
- **Tier 3** (internal): `scheduler`, `title_llm`
- Not in any tier list (top-level field on `Config`): `scoring_priority`

At runtime, all sections are flat fields on `Config` (e.g. `config.analysis`).
Both flat and nested YAML formats are accepted.

## Conventions

- **Max file length**: 800 lines soft / 1000 hard (enforced in CI via `make file-length`)
- **Max complexity**: Xenon grade C (<=20 cyclomatic complexity, `make complexity`)
- **Cognitive complexity**: complexipy ≤15 per function (`make cognitive-complexity`)
- **Makefile**: Single source of truth for all commands (CI, pre-commit, CLAUDE.md)
- **Composition**: Top-level orchestrators compose service objects via constructor injection
- **Re-export shims**: Only in `__init__.py` — never in regular modules
- **No `_`-prefixed overflow files**: All files have descriptive names
- **Private helpers**: Prefixed with `_`, same package
- **Tests**: `tests/` directory, run with `make test`
- **Integration tests**: run manually with `make test-integration*` (per-suite folders under `tests/integration/`, see CLAUDE.md); also run on the self-hosted GPU runner. Not a pre-commit hook.
- **Pre-commit**: Run `make ci` before committing
