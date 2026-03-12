# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-03-12


### Features
- feat: add 6 quality improvements across tests, logging, config errors, benchmarks, and docs (0c6281c)
### Fixed
- fix(release): auto-update CHANGELOG.md on release (b446c3d)
- fix(test): reduce run_id uniqueness sample to avoid birthday-paradox collisions (2be17a6)
- fix(test): mock FFmpeg in assembler music test for CI compatibility (51dfc71)
- fix(deps): move pytest-benchmark to optional-dependencies dev (a2310af)
- fix(docs): replace em dashes with colons per voice guide, refactor tests to test flows (ae7f1e9)
### Changed
- ci(changelog): add PR-based changelog update workflow (8ec0f95)
- docs(changelog): add unreleased entries for quality improvements (f6a7dee)
- docs: add CPU-only mode page documenting GPU-free operation (2723e96)
- test: add 152 tests for run_id, transitions, text_builder, tracker, system_info, styles, and blend (9b4d11b)
- test: add API client, ClipExtractor, and duplicate detection tests with parametrized edge cases (a98182d)
- test: improve test quality with edge cases, parametrize, and isolation fixes (147a155)


### Added
- Multi-provider music pipeline: ACE-Step → MusicGen fallback with independent enable/disable
- ACE-Step 1.5 REST API integration with explicit BPM, key, time signature parameters
- Dense caption templates for 10 mood/genre presets (lofi, pop, cinematic, jazz, etc.)
- Video timeline → ACE-Step section tag mapping ([Intro], [Verse], [Chorus], [Bridge], [Outro])
- MusicPipeline orchestrator with automatic backend fallback
- Stem separation via MusicGen Demucs independent of generation backend
- Structured JSON logging option for production (`IMMICH_MEMORIES_LOG_FORMAT=json`)
- User-friendly config validation error messages for YAML parse and Pydantic errors
- Auto-generated CLI reference docs from Click command tree (`make docs-cli`)
- CPU-only mode documentation: detailed feature comparison table, performance expectations
- Performance benchmarks with pytest-benchmark (hashing, scoring, config, duplicates)
- 300+ new tests: pipeline integration, assembler, CLI smoke, UI state, API client, clip transitions, run tracking, system info, title styles, frame blending

### Fixed
- Release pipeline now auto-updates CHANGELOG.md on each release

### Removed
- Pixabay music source (replaced by AI generation backends)

### Changed
- Default music source changed from "pixabay" to "musicgen"
- ACE-Step default API port changed from 7860 (Gradio) to 8000 (REST API)

## [0.2.0] - 2026-03-08

First public release.

### Added
- Immich API integration: connect to your self-hosted Immich server and fetch your video library
- Video analysis with PySceneDetect for natural scene boundary detection
- Duplicate detection using perceptual video hashing
- Interest scoring based on faces, motion, stability, and content
- Hardware acceleration: NVIDIA NVENC, Apple VideoToolbox, Intel QSV, VAAPI
- Apple Vision framework integration for Neural Engine-accelerated face detection on macOS
- NiceGUI-based 4-step wizard UI (Configuration, Clip Review, Generation Options, Preview & Export)
- CLI for headless automation and scripting
- Docker support for containerized deployment
- Smart target duration: scales with time period (year: 10 min, half-year: 6 min, quarter: 4 min, month: 2 min)
- Non-favorite ratio limit: configurable max percentage of non-favorite clips (default 25%)
- Time-based duplicate clustering: clips within 2 minutes grouped if visually similar
- LLM content analysis: optional AI-powered scoring via Ollama or OpenAI
- Moondream model support with automatic 2-image limit (2048 token context)
- Speed optimization: video downscaling to 480p for analysis (3-5x faster)
- Memory optimization: aggressive garbage collection, bounded progress tracking, on-demand thumbnail loading
- Resume support: previously analyzed clips cached and shown on restart
- Smooth transitions: configurable transition buffer (0.5s default) for crossfade effects
- Review mode: review and refine selected clips after analysis with deselection support
- Scene detection enabled by default with configurable max/min segment durations
- Audio-aware cutting: silence detection for natural cut points
- AI music generation via MusicGen and ACE-Step with mood-based soundtrack creation
- Smart audio ducking: music automatically lowers when speech/sounds detected
- Flexible output: Portrait (9:16), Landscape (16:9), Square (1:1), and Auto detection
- Face-aware smart cropping keeps subjects centered during aspect ratio conversion
- Kubernetes deployment with NVIDIA GPU support
- Terraform module for infrastructure-as-code deployment

### Fixed
- Infinite loop in Duration mode: auto-birthday feature now only triggers in Year mode
- LLM token truncation for Moondream: limited to 2 images to fit 2048 token context
- LLM copying prompt template: rewrote prompt to prevent placeholder copying
- Progress UI preview sizing: adjusted column ratio to [2:1]
- LLM results display: fixed whitespace handling and added partial data extraction
- iPhone spatial audio (apac codec): explicit stream selection (`-map 0:a:0`)
- Preview extraction: `-map 0:a:0?` to skip unsupported spatial audio streams
- Type error in `content_analyzer.py` for OpenAI content list

[Unreleased]: https://github.com/sam-dumont/immich-video-memory-generator/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/sam-dumont/immich-video-memory-generator/compare/v0.2.0...v0.5.0
[0.2.0]: https://github.com/sam-dumont/immich-video-memory-generator/releases/tag/v0.2.0
