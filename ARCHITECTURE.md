# Architecture Guide

> This document is optimized for LLM consumption. Reference it from CLAUDE.md
> to avoid re-reading the full codebase each session.

## Overview

Immich Memories generates video compilations from an Immich photo library.
The public lifecycle of one run (`operations/phases.py`, `OperationalPhase`):
**discovery -> download -> analysis -> selection -> render -> music -> delivery -> complete**.
Selection is the story-first editorial route, the only one: `generate` (or the Memory page's
Cut) -> `build_smart_pipeline(editorial_context)` (`analysis/editorial_runtime.py`) ->
`SmartPipeline.run_editorial_source()` -> `RuntimeEditorialPlanner.plan_source()`, which reports
six stages: **Preparing source metadata -> Reading event evidence -> Reading the period account
-> Building editorial cards -> Editing the memory -> Validating selected source timing**. Every
attempt is durable under `<cache>/editorial-runs/<key>/attempts/<id>/`
(`operations/editorial_attempt.py`, an OS lease tells interrupted from slow); the facts and banks
it reads live in `<cache>/annotations.sqlite` (`store/`). The design is summarised in
`docs/designs/2026-09-10-story-first-selection.md`.

## Two Trees

`src/immich_memories/` is the app. `services/inference/immich_memories_inference/` is a second
top-level package — the inference service, which serves the encoder, the six public heads and the
two detectors over HTTP (`/ping`, `/health`, `/facts`) in its own image with its own device
variant (`docker/Dockerfile.inference`, `docker/hwaccel.inference.yml`). It imports the app's
triage engine and detector module rather than reimplementing them, which is what keeps a fact
computed there identical to one computed in process; two import-linter contracts hold the
direction of that dependency and keep the UI and CLI out of it. The service is not a distribution:
the image puts it on `PYTHONPATH`, and `pythonpath` in `[tool.pytest.ini_options]` does the same
for the suite. Its `seeding.py` fills a cold cache volume from the app's `pinned_models.py` table,
so a fresh PVC needs no `kubectl cp`. Design:
`docs/implementation-plans/2026-09-11-phase5-inference-service.md`.

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

**VideoAssembler** (processing/video_assembler.py) composes 5 services:
- `FFmpegProber` (ffmpeg_prober.py): duration/resolution probing via ffprobe
- `ClipEncoder` (clip_encoder.py): per-clip trimming and re-encoding
- `AssemblyEngine` (assembly_engine.py): strategy-based multi-clip assembly
- `AudioMixerService` (audio_mixer_service.py): background music mixing
- `TitleInserter` (title_inserter.py): title screen concatenation
  - composes `TitleBackgroundRenderer` (title_background_renderer.py) for the pre-rendered
    clip a title deblurs out of, and builds one `TitleDividerPlanner`
    (title_divider_planner.py) per memory to place month/year/location cards

**SmartPipeline** (analysis/smart_pipeline.py) is the pipeline surface the CLI and the UI
drive. On the production route `build_smart_pipeline(editorial_context)` (editorial_runtime.py)
hands it a `RuntimeEditorialPlanner`, and `run_editorial_source()` runs preparation, the two
readings, the structure and story planners and the timing certification, then projects the plan
into a `PipelineResult` (editorial_projection.py). The route's seams are Protocol-typed ports
rather than services: `EditorialRuntimePorts` (editorial_runtime_ports.py: providers, people
loader), `ProductionPostCardBackend` (editorial_runtime_backend.py), `StructurePlannerPorts`
(editorial_structure_contract.py: judges, banks, audience gate).

`SmartPipeline` composes nothing else: the only seams it reads are `PipelineConfig.hdr_only`
and the planner, and `ProgressTracker` (progress.py) is the run clock the stage reporter reads.

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

**KernelTitleRenderer** (titles/renderer_kernels.py) owns the background and the per-frame
GPU pipeline, and composes 3 services (all take Protocol-typed config/buffers):
- `ParticleField` (kernel_particles.py): bokeh drift and fireworks physics, CPU numpy only
- `TitleTextRenderer` (kernel_text.py): SDF and PIL text compositing onto the frame buffer
- `AnimatedBlur` (kernel_blur.py): the deblur's Gaussian, decimated and held between frames

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
- `generate_delivery.py`: Immich upload of a finished artifact + delivered/pending/failed run state

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
│   ├── compatibility.py        # Immich API-version compatibility policy (v2/v3 resolution)
│   └── models.py               # API data models (Asset, Person, etc.)
│
├── photos/                     # Photo-to-video animation (converts stills to .mp4 clips)
│   ├── __init__.py             # Public API re-exports
│   ├── renderer.py             # Frame-by-frame renderer: Ken Burns, face_aware_pan, render_split (parked)
│   ├── animator.py             # Photo source prep: HEIC decode, downscale cap, HDR detection
│   ├── photo_pipeline.py       # Render one photograph as a Ken Burns clip, streamed to FFmpeg
│   ├── ultrahdr.py             # Ultra HDR JPEG (Android/Pixel): MPF parser, gain map, ISO 21496-1
│   └── burst_dedup.py          # One photo per burst: near-duplicates shot within minutes of each other
│
├── memory_types/               # Memory type presets & factory
│   ├── __init__.py             # Public API re-exports
│   ├── registry.py             # MemoryType enum
│   ├── presets.py              # ScoringProfile, PersonFilter, MemoryPreset
│   ├── date_builders.py        # build_season(), build_month(), build_on_this_day()
│   └── factory.py              # Registry + preset factories; Album is handled by cli/_album_generation.py
│
├── analysis/                   # Selection: the story-first editorial route
│   ├── smart_pipeline.py       # SmartPipeline: run_editorial_source() is the production entry
│   ├── editorial_runtime.py    # RuntimeEditorialPlanner + build_smart_pipeline(); _ports.py, _backend.py beside it
│   ├── editorial_orchestration.py  # TextEditorialPlanner: episodes -> period account -> cards -> edit
│   ├── editorial_rule_episodes.py  # Factual episode cards / omitted thesis; no semantic-bank writes
│   ├── editorial_rule_reader.py    # Rules for worthiness, grouping and standing; shared allocation
│   ├── editorial_shareability_tiers.py  # Audience evidence policy for reduced preparation tiers
│   ├── editorial_preparation*.py   # Annotation preparation: captions, public heads, detectors, pixel facts
│   ├── selection_source*.py    # The canonical source model: admission, provenance, groups, invariants
│   ├── text_episode_reader.py  # Reading event evidence (paged, banked); period_insight*.py = the account
│   ├── editorial_story_*.py    # Story reading, weighing, slots, shortlist, carriers: the story planner
│   ├── editorial_page_recovery.py  # Bounded ask/retry/repair for a stage that reads its own JSON envelope
│   ├── provider_failure.py     # What a 4xx/5xx means: refused, come back later, down, or a bad credential
│   ├── llm_single_flight.py    # One paid answer per judgment key, however many readers ask at once
│   ├── editorial_structure_*.py    # The structure planner: wall, memory-worthy + standing gates, audience, record
│   ├── editorial_projection.py # Plan -> PipelineResult, and the stage reporter
│   ├── provider_health.py      # ProviderHealth: what a provider's answer says about its availability (preflight)
│   ├── selection_trace.py      # Per-stage funnel record: what each filter received and let through
│   ├── progress.py             # ProgressTracker: the run clock the stage reporter reads
│   ├── trip_detection.py       # GPS-based trip detection (clustering, geocoding)
│   ├── trip_discovery.py       # Shared UI/CLI all-asset discovery, including year-boundary trips
│   ├── special_day.py          # Which days had something happen: active hours, not photo volume
│   ├── prepared_captions.py    # Exact-producer caption reads for music and special-day text calls
│   ├── special_day_title.py    # What a day may be called: the grounding guard, the re-ask, the fallback
│   ├── album_source.py         # Album mode: the album is the candidate pool, nothing is searched for
│   ├── source_filter.py        # Drop doorbell / dashcam / screen-recorder uploads by filename
│   ├── source_quality.py       # Drop messaging re-encodes: sub-1080p with no camera EXIF
│   ├── llm_failures.py         # Separate "the model could not answer" from a bug in the calling code
│   ├── request_heartbeat.py    # RequestHeartbeat: periodic log line for long-outstanding HTTP calls
│   ├── duplicate_hashing.py    # Perceptual hashing for duplicates
│   ├── thumbnail_prefetch.py   # cached_preview_bytes(): the one preview reader the editorial modules share
│   ├── apple_vision.py         # macOS Vision framework face detection (smart crops)
│   ├── apple_vision_image.py   # Vision image conversion helpers
│   ├── llm_query.py            # The live transport: one prompt, one connection, one answer
│   ├── llm_wire.py             # The two request dialects, what a reply says, and the reasoning budget
│   ├── llm_batch.py            # A stage's independent prompts as one provider batch (half price, async): the two wire dialects and their transports
│   ├── llm_providers.py        # Named providers: their URL, their adapter, the way they reason
│   ├── llm_usage_record.py     # llm-usage.json: the run's unrounded token spend, split per model
│   ├── live_photo_pipeline.py  # Keep a Live Photo's video half out of the video pool
│   └── motion_rendering.py     # What a photograph could show as motion, if the memory wants it
│
├── processing/                 # Video processing & assembly
│   ├── video_assembler.py      # VideoAssembler (composes 5 services)
│   ├── assembly_engine.py      # AssemblyEngine: strategy-based multi-clip assembly
│   ├── assembly_config.py      # Dataclasses: AssemblySettings, AssemblyClip, etc.
│   ├── streaming_assembler.py  # StreamingEncoder + assemble_streaming(): low-memory 4K assembly
│   ├── streaming_frame_decoder.py # FrameDecoder / make_decoder(): clip -> normalized raw frames
│   ├── streaming_frame_blender.py # FrameBlender: frames -> sink, crossfades, progress/preview
│   ├── streaming_audio.py      # Streaming audio processing helpers
│   ├── ffmpeg_prober.py        # FFmpegProber: ffprobe-based duration/resolution
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
│   ├── clip_caption.py         # The per-clip date/place caption: text and geometry, no decoding
│   ├── frame_sampling.py       # One cached still-frame sampler for mood, title colours and previews
│   ├── frame_preview.py        # Frame extraction for previews
│   ├── hdr_utilities.py        # HDR detection & conversion filters
│   ├── scaling_utilities.py    # Resolution, aspect ratio, smart crop
│   ├── ffmpeg_runner.py        # FFmpeg execution with progress
│   ├── hardware.py             # Hardware detection (GPU, encoders)
│   ├── hardware_detection.py   # Hardware detection backends
│   ├── hardware_encode.py      # VAAPI/QSV device init + hwupload for built commands
│   ├── rate_control.py         # CRF -> per-encoder constant-quality flags
│   └── live_photo_merger.py    # Live Photo merging
│
├── audio/                      # Audio processing
│   ├── mixer.py                # Audio mixing & ducking
│   ├── mixer_class.py          # AudioMixer class
│   ├── mixer_helpers.py        # Mixing helper functions
│   ├── mood_analyzer.py        # Mood detection for music matching
│   ├── mood_analyzer_backends.py # Mood analysis backends
│   ├── music_generator.py      # AI music generation orchestrator
│   ├── music_generator_client.py # Music generation client
│   ├── music_generator_models.py # Music generation data models
│   ├── music_sources.py        # Music source providers (local library)
│   ├── text_mood.py            # Banked music judgment from saved cut text; private answering-route record
│   ├── music_pipeline.py       # Multi-provider pipeline (ACE-Step -> MusicGen fallback)
│   ├── bundled_music.py        # The 28 bundled royalty-free tracks (`music` extra), used with no backend
│   ├── track_tempo.py          # Measure a bundled track's tempo (numpy onset autocorrelation, no librosa)
│   ├── beat_grid.py            # Ask the generator for a tempo whose beat divides the photo cut cadence
│   ├── mastering.py            # Loudness + high-shelf pass so a generated track sits under video
│   └── generators/             # Music generation backends
│       ├── base.py             # MusicGenerator ABC + StemSeparator Protocol
│       ├── factory.py          # Generator factory
│       ├── memory_budget.py    # Will this ACE-Step profile fit in RAM? (checked before jetsam decides)
│       ├── musicgen_backend.py # MusicGen API (generation + remote Demucs stems)
│       ├── ace_step_backend.py # ACE-Step lib/API (mode choice, captions, REST protocol)
│       ├── ace_step_runtime.py # ACE-Step in-process handlers: device, MLX/torch memory, one render
│       ├── ace_step_captions.py # Dense caption templates
│       └── demucs_local.py     # Local Demucs stem separation (in-process)
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
│   ├── renderer_kernels.py     # KernelTitleRenderer: background + frame pipeline
│   ├── kernel_particles.py     # ParticleField: bokeh drift / fireworks physics
│   ├── kernel_text.py          # TitleTextRenderer: SDF + PIL text compositing
│   ├── kernel_blur.py          # AnimatedBlur: quarter-res deblur Gaussian, held while it stands
│   ├── gpu_kernel_backend.py   # The only `import quadrants as ti` in the tree (behind the probe)
│   ├── kernels.py              # GPU kernels + lazy compilation (init_kernels)
│   ├── kernel_backend_probe.py # The gate: which arch can dispatch here, asked without loading
│   ├── kernel_video.py         # GPU title video creation
│   ├── ffmpeg_pipe.py          # Feed raw frames to FFmpeg without deadlocking on an unread stderr
│   ├── safe_zones.py           # Keep vertical titles clear of the Reels/Shorts/TikTok button rail
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
│   ├── generate_options.py     # `generate`'s flags, grouped; group order is the --help order
│   ├── generate_resolution.py  # What those flags mean against the config, presets and conflicts
│   ├── _analyze_export.py      # `analyze`, `export-project`
│   ├── config_cmd.py           # `config`, `years`, `preflight`
│   ├── people_cmd.py           # `people` scan/show
│   ├── models_cmd.py           # `models fetch`
│   ├── prepare_cmd.py          # `prepare`
│   ├── scheduler_cmd.py        # `scheduler list/status/start`
│   ├── auto_cmd.py             # `auto suggest/run/history/status/install/test-notification`
│   ├── special_days_cmd.py     # `discover-days` and `days-due`: the days worth a memory of their own
│   ├── cache_cmd.py            # `cache stats/export/import/backup`
│   ├── titles.py               # `titles test`, `titles fonts`
│   ├── runs.py                 # `runs list/show/story/why/stats/storage/delete`
│   ├── music_cmd.py            # `music search/analyze/add`
│   ├── hardware_cmd.py         # `hardware` info display
│   ├── _helpers.py             # Shared console/print utilities
│   ├── _generation_preview.py  # Plain-text summary for read-only generation planning (--dry-run)
│   ├── _config_errors.py       # Config error formatting
│   ├── _flags.py               # Shared validation for flags more than one command takes
│   ├── _pipeline_runner.py     # Run SmartPipeline over the fetched assets + generate
│   ├── _editorial_context.py   # CLI flags + presets -> one EditorialRunContext
│   ├── _run_timeline.py        # The run's timeline: selection budget, then the settled plan
│   ├── _asset_fetch.py         # What a memory asks Immich for: videos, Live Photos, stills
│   ├── _album_generation.py    # Album mode: an Immich album is the candidate pool
│   ├── _llm_title.py           # Opt-in LLM title on the CLI path (the wizard's default differs)
│   ├── _trip_generation.py     # Trip detection, selection, per-trip generation
│   ├── _trip_display.py        # Trip table formatting & selection logic
│   ├── _date_resolution.py     # Date range resolution for memory types
│   ├── _generate_display.py    # Params table + result printing for `generate`
│   └── _live_display.py        # Rich Live interactive progress display
│
├── ui/                         # NiceGUI web interface
│   ├── app.py                  # App setup & routing
│   ├── auth.py                 # Auth middleware, credential verification, session helpers
│   ├── auth_oidc.py            # OIDC client (authlib starlette integration, singleton)
│   ├── health_api.py           # GET /health, /health/live, /health/ready — probe payloads + snapshot cache
│   ├── trigger_api.py          # POST /api/trigger — runs what `auto run` decides, 202 + status URL
│   ├── reverse_proxy.py        # Secure cookie + trusted X-Forwarded-* kwargs for ui.run
│   ├── state.py                # Shared UI state
│   ├── session_storage.py      # Expire the storage-user-*.json files NiceGUI writes but never cleans
│   ├── theme.py                # UI theme
│   ├── components.py           # Shared UI components
│   ├── nicegui_compat.py       # Compatibility helpers for NiceGUI background work
│   └── pages/
│       ├── login.py                # Login page (basic form + OIDC SSO button)
│       ├── memory.py               # The Memory page router: brief, cut in progress, result
│       ├── memory_brief.py         # The brief: type select, its params, Advanced, Cut
│       ├── memory_duration.py      # The duration line: the type's answer or an override
│       ├── memory_run.py           # The cut that outlives its page: arm, poll the attempt, cancel, recover
│       ├── memory_story.py         # The story view: thesis, stories, carriers with reasons
│       ├── memory_story_data.py    # The only UI reader of plan.private.json -> frozen StoryView
│       ├── step1_config.py         # Immich connection panel + custom date range
│       ├── step1_cache.py          # Cache management UI
│       ├── step1_presets.py        # The parameters each memory type asks for
│       ├── step1_tabs.py           # Step 1 tab layout
│       ├── step2_review.py         # Clip review orchestration
│       ├── step2_loading.py        # Loading state UI
│       ├── step2_helpers.py        # Shared step2 utilities
│       ├── clip_grid.py            # Clip card grid display
│       ├── clip_review.py          # Clip refinement controls
│       ├── clip_pipeline.py        # The blocking cut worker and its editorial context
│       ├── pipeline_title.py       # Pipeline title display
│       ├── step3_options.py        # Assembly options
│       ├── _step3_music_preview.py # Music preview controls
│       ├── step4_export.py         # Export & download
│       ├── _step4_generate.py      # Generation logic
│       ├── step4_recovery.py       # Reload recovers a run that outlived the page
│       ├── _step4_upload.py        # Upload-back to Immich
│       ├── settings_config.py      # Settings page
│       └── settings_people.py      # The companion editor: confirm who's who, flag twins
│
├── tracking/                   # Run history & telemetry
│   ├── run_database.py         # SQLite run storage
│   ├── run_database_rows.py    # SQLite row <-> model conversion
│   ├── run_lifecycle_errors.py # Refused lifecycle transitions and their diagnosis
│   ├── run_tracker.py          # Pipeline run tracking
│   ├── run_id.py               # Run ID generation
│   ├── models.py               # Run/phase data models
│   └── system_info.py          # System info collection
│
├── cache/                      # Analysis caching system
│   ├── __init__.py             # Re-exports public API
│   ├── database.py             # VideoAnalysisCache: owns cache.db's schema; the legacy segment tables it still reads
│   ├── schema_migrator.py      # SchemaMigrator: schema ladder v1..vN, DDL
│   ├── versions.py             # SCHEMA_VERSION / ANALYSIS_VERSION (independent)
│   ├── migration_sql.py        # Transactional migration helpers
│   ├── migration_v11.py … v23.py # One module per schema migration (no v18, no v20)
│   ├── asset_score_cache.py    # The legacy photo scorer's table, still read by `cache stats/export/import`
│   ├── judgment_cache.py       # Reasoning-mode LLM verdicts, keyed by the exact prompt asked
│   ├── thumbnail_cache.py      # File-based thumbnail storage
│   ├── disk_budget.py          # LRU-by-mtime eviction that holds a cache directory to a size cap
│   └── video_cache.py          # Downloaded video file cache
│
├── scheduling/                 # Scheduled memory generation
│   ├── engine.py               # Scheduler: cron parsing, next job calculation
│   ├── executor.py             # resolve_schedule_params(): schedule entry -> generation params
│   ├── daemon.py               # Daemon loop (foreground, SIGINT/SIGTERM)
│   └── models.py               # Scheduling data models
│
├── store/                      # The annotation store: every banked fact and reading
│                               # (annotations.sqlite; see docs/research for the design)
│
├── triage/                     # The pinned DINOv2 ONNX encoder and its six context heads
│
├── people/                     # The library's people graph (counts and dates, no pixels)
│   ├── signatures.py           # Tiers, onset, twins, duplicates, dyads, owner curve pairing
│   ├── graph.py                # build_graph(): Immich roster + co-occurrence -> PeopleGraph
│   ├── companion.py            # ~/.immich-memories/people.yaml; confirmed beats inferred
│   └── editor.py               # The companion editor's model: the file as rows, and back
│
├── automation/                 # Smart automation (auto suggest/run)
│   ├── __init__.py             # Public API re-exports
│   ├── candidates.py           # Memory candidate detection
│   ├── candidate_scorer.py     # Candidate scoring & ranking
│   ├── candidate_discovery.py  # CandidateDiscovery: one library snapshot -> ranked candidates
│   ├── event_detectors.py      # Event-based detectors (activity bursts)
│   ├── calendar_detectors.py   # Calendar-based detectors (monthly, yearly)
│   ├── special_day_scan.py     # Scheduled scan for days worth resurfacing (skips holidays and trips)
│   ├── variety.py              # Cadence and rotation rules for candidates
│   ├── failure_backoff.py      # Keep a candidate that keeps failing out of the nightly slot
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
│   ├── runtime_provenance.py   # Which code a scheduled job actually ran: version, commit, checkout age
│   └── system_scheduler.py     # OS scheduler integration (launchd/systemd/cron)
│
├── operations/                 # Public lifecycle contract + read-only ops reports
│   ├── auto_output.py           # Private complete child transcripts, addressed by automation attempt
│   ├── candidate_fates.py       # Saved pool outcomes + decision-log reader shared with runs why
│   ├── phases.py               # OperationalPhase / PhaseEvent: stable outer lifecycle
│   └── storage_report.py       # build_storage_report(): output + cache storage inventory (`runs storage`)
│
├── planning/                   # Media-aware duration planning
│   └── auto_duration.py        # resolve_trip_auto_duration(): trip auto-duration heuristics
│
├── config.py                   # YAML configuration management (re-exports)
├── config_loader.py            # Config loading logic
├── config_presets.py           # Named presets (`preset: fast`) that fill several knobs at once
├── config_models.py            # Resources a run uses: Immich server, cache, hardware (+ expand_env_vars)
├── config_models_analysis.py   # Source admission and the expected seconds per clip
├── config_models_auth.py       # Authentication config model (basic, OIDC, header)
├── config_models_automation.py # Running unattended: trips, automation, notifications, upload
├── config_models_llm.py        # LLM provider settings (shared by analysis and titles)
├── config_models_render.py     # What the video looks like: defaults, output, title screens, photos
├── config_models_server.py     # UI server bind settings + secure-by-default host rule
├── config_models_soundtrack.py # Music under a memory: local library, MusicGen, ACE-Step
├── generate.py                 # End-to-end generation orchestrator
├── generate_clips.py           # Clip extraction, probing, cleanup
├── generate_delivery.py        # Immich upload + delivered/pending/failed run state
├── generate_downloads.py       # Parallel asset downloads
├── generate_music.py           # Music resolution, AI generation, audio mixing
├── generate_photos.py          # Photo rendering, budget allocation, clip merging
├── generate_privacy.py         # GPS anonymization, fake names/cities, trip titles
├── generate_progress.py        # Adapters from pipeline progress to caller-supplied callbacks
├── generate_settings.py        # Assembly/title settings, assembler creation, music, upload call
├── generate_timeline.py        # Final-duration validation + content budget guards
├── pinned_models.py            # One digest-pinned artifact table: `models fetch` and the inference service both read it
├── filename_builder.py         # Output filename generation
├── timeperiod.py               # Date range utilities
├── security.py                 # Input sanitization
├── i18n.py                     # Internationalization
├── preflight.py                # Dependency checks
├── logging_config.py           # Logging setup
└── _version.py                 # Auto-generated by hatch-vcs (do not edit)
```

## Key Classes & Their Relationships

### Independent reader work

`editorial_reader_concurrency.py` bounds model-reader jobs and copies the run's
cancellation and metrics context into each worker. Each job owns a text judge
and audit directory; completed call records are merged in source order.
`editorial_block_votes.py` commits vote-bank updates on the caller thread.
`editorial_story_planner.py` overlaps event inventories, retaining sequential
pages within each event. Prompts and judgment identities do not include the
concurrency setting.

How many jobs overlap comes from `llm_providers.reader_concurrency`, which reads
the endpoint when `llm.reader_concurrency` is unset: one job for a model on this
machine or this private network, four for a hosted one. `llm_single_flight.py`
keeps the bank's deduplication under concurrency, so two jobs carrying the same
judgment key cannot both pay for it. `provider_failure.THROTTLE` is the shared
pause a 429 puts on every reader at once, and `retry_wait` spreads each caller's
own wait so they do not retry in lockstep.

### Pipeline Flow (story-first)

Videos and photos are one pool, and the editor cuts from it:

```
generate / Memory page Cut
  └── build_smart_pipeline(editorial_context)           (editorial_runtime.py)
        └── SmartPipeline.run_editorial_source()        (smart_pipeline.py)
              └── RuntimeEditorialPlanner.plan_source()
                    ├── EditorialAttempt: lease + status.private.json   (operations/editorial_attempt.py)
                    ├── source model: fetch_full_window_source -> prepare_editorial_source
                    ├── "Preparing source metadata": prepare_editorial_annotations
                    ├── TextEditorialPlanner.plan_prepared             (editorial_orchestration.py)
                    │     ├── "Reading event evidence": episode reader + cull
                    │     ├── "Reading the period account": the thesis
                    │     ├── "Building editorial cards": build_moment_cards -> moment wall
                    │     └── "Editing the memory": plan_structure -> select_story_first
                    ├── "Validating selected source timing": bind_editorial_timeline
                    └── project_source_rendering -> render-projection.private.json
              └── PipelineResult with editorial_selections  (editorial_projection.py)
```

### Assembly Flow

```
VideoAssembler.assemble()
  ├── AssemblyEngine resolves target resolution and transitions
  └── AssemblyEngine → streaming assembly → FFmpeg execution

VideoAssembler.assemble_with_titles()
  ├── TitleScreenGenerator → title/month/ending screens
  ├── assemble() → main content
  └── AudioMixerService → background music
```

## Configuration

- `Config` (config_loader.py): loaded from `~/.immich-memories/config.yaml`, tiered YAML (see above)
- `AssemblySettings` (assembly_config.py): video assembly parameters
- `PipelineConfig` (smart_pipeline.py): the per-run switches the editorial route reads

## Data Flow

```
Immich API → Asset models → ClipExtractor → VideoClipInfo
  → SmartPipeline.run_editorial_source → EditorialSelection (asset, interval, render mode)
  → ClipWithSegment → VideoAssembler → final .mp4
```

## Configuration Tiers

Config is organized in 3 tiers (see `config_loader.py`):

- **Tier 1** (top-level YAML): `immich`, `defaults`, `output`, `audio`, `title_screens`, `cache`, `upload`, `trips`, `photos`
- **Tier 2** (under `advanced:` in YAML, `_TIER2_SECTIONS`): `analysis`, `hardware`, `llm`, `musicgen`, `ace_step`, `server`, `auth`, `automation`, `notifications`, `triage`, `editorial`, `inference`
- **Tier 3** (internal): `scheduler`, `title_llm`

At runtime, all sections are flat fields on `Config` (e.g. `config.analysis`).
Both flat and nested YAML formats are accepted.

The tiers are a YAML layout, not a code layout. The section models are grouped by
domain across the `config_models*.py` modules (resources, analysis, render,
soundtrack, automation, llm, auth, server), and `Config` in `config_loader.py`
assembles them into one flat settings object. A file naming a key of the removed
clip scorer (`_REMOVED_CONFIG_KEYS`) is refused at load with a message naming it.

## Render worker (S1)

`services/render-worker/immich_memories_render_worker/` owns the authenticated
versioned job API. `admission.py` names a job after its cut (`memory_key` plus
the binding digest) and refuses an envelope that drifted from the binding it
carries, before any byte is fetched. `jobs.py` serializes GPU work, validates
artifacts, enforces the job deadline and sweeps scratch at boot; `store.py`
keeps atomic job transitions behind a repository contract ready for a future
PostgreSQL implementation, with a per-job JSON record so a restart can say a
render died with its process. `native.py` and `native_plan.py` adapt selected
cuts to the existing generator: a missing NVENC is a recorded degradation, a
changed selection is a refusal. The app does not call this service yet;
orchestration and deployment belong to later slices of #931.

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

The web sidebar links Memory, Suggestions, Runs, Media pool and Settings.
`ui/pages/suggestions.py` uses `AutoRunner`; `ui/pages/runs.py` reads `RunDatabase`
and the shared run index/storyboard. Neither page owns a separate job store.
