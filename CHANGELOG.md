# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Smart Target Duration**: Default video duration now scales with time period
  - Year: 10 min, Half-year: 6 min, Quarter: 4 min, Month: 2 min, < Month: 1 min
  - Warning shown when target exceeds available content
- **Non-Favorite Ratio Limit**: Configurable maximum percentage of non-favorite clips (default 25%)
  - New UI slider under "Prioritize favorites" option
  - Prevents "filling up" compilations with less interesting content
- **Time-Based Duplicate Clustering**: Clips within 2 minutes are grouped if visually similar
  - Catches different framings of the same scene
  - Increased hash threshold from 8 to 14 for more lenient matching
- **Improved LLM Content Analysis**:
  - Moondream model support with automatic 2-image limit (fits 2048 token context)
  - Robust JSON parsing with regex fallback extraction
  - Simplified prompt to prevent template copying
- **Better Audio-Aware Cutting**:
  - Increased cut quality bonus from 5% to 15%
  - Lowered silence threshold from -30dB to -40dB (more sensitive)
  - Added configurable min_silence_duration (default 0.2s)
- Initial project structure with Immich API integration
- Video analysis with PySceneDetect for scene detection
- Duplicate detection using perceptual video hashing
- Interest scoring based on faces, motion, and stability
- Hardware acceleration support (NVIDIA NVENC, Apple VideoToolbox, Intel QSV, VAAPI)
- Apple Vision framework integration for GPU-accelerated face detection on macOS
- NiceGUI-based 5-step wizard UI
- CLI for headless automation
- Docker support for containerized deployment
- Comprehensive documentation and contribution guide
- **Speed optimization**: Video downscaling to 480p for analysis (~3-5x faster)
- **Memory optimization**: Aggressive garbage collection, bounded progress tracking, on-demand thumbnail loading
- **Resume support**: Previously analyzed clips displayed on restart with "Use Cached Analysis" button
- **Smooth transitions**: Configurable transition buffer (0.5s default) for crossfade effects
- New `downscaler.py` module for fast video downscaling with ffmpeg
- New `render_cached_analysis_summary()` component for displaying cached analyses
- Config options: `enable_downscaling`, `analysis_resolution`, `transition_buffer`
- **Review Selected Clips UI**: New interface after pipeline completion to review and refine selected clips
  - Shows only clips selected by the analysis pipeline
  - Allows deselecting clips (they remain in list for potential re-selection)
  - Provides segment time refinement with range sliders
  - Includes bulk actions (set all to first/middle/custom seconds)
- **Scene Detection Integration**: Natural scene boundaries replace fixed 3-second segments
  - Enabled by default via `use_scene_detection: true`
  - Long scenes automatically subdivided (configurable `max_segment_duration`)
  - Short scenes filtered (configurable `min_segment_duration`)
  - Graceful fallback to fixed segments if detection fails
- **LLM Content Analysis**: Optional AI-powered content understanding for scoring
  - Supports Ollama (local, privacy-friendly) and OpenAI
  - Analyzes video frames to detect activities, subjects, emotions, and quality
  - Adds `content_score` to segment scoring (configurable weight)
  - Disabled by default for performance (`content_analysis.enabled: false`)
  - New config section `content_analysis` with full Ollama/OpenAI settings
- New `ContentAnalysisConfig` class for LLM settings
- New `create_scorer_from_config()` factory function
- Environment variable documentation with examples for all config options

### Fixed
- **Infinite loop in Duration mode**: Auto-birthday feature now only triggers in Year mode
- **LLM token truncation for Moondream**: Limited to 2 images to fit 2048 token context
- **LLM copying prompt template**: Rewrote prompt to prevent "brief description" placeholder copying
- **Progress UI preview too large**: Adjusted column ratio to [2:1] for smaller video preview
- **LLM results not displayed**: Fixed whitespace handling and added partial data extraction
- Silence detection now handles iPhone spatial audio (apac codec) by using ffmpeg with explicit stream selection (`-map 0:a:0`)
- Preview extraction uses `-map 0:a:0?` to skip unsupported spatial audio streams
- Type error in `content_analyzer.py` for OpenAI content list

### Changed
- Updated Python requirement to 3.12+ (3.13 recommended)
- Switched to uv for package management
- Thumbnails now loaded on-demand from cache instead of stored in session state
- Analysis uses downscaled video for scoring, original video for preview extraction
- GC runs after each clip analysis to prevent memory buildup
- `MomentScore` now includes `content_score` field
- `SceneScorer` now supports `content_weight` and `content_analyzer` parameters
- `sample_and_score_video()` uses scene detection by default with fallback

## [0.1.0] - Unreleased

Initial release.

[Unreleased]: https://github.com/sam-dumont/immich-video-memory-generator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sam-dumont/immich-video-memory-generator/releases/tag/v0.1.0
