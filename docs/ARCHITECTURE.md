# Architecture Overview

This document describes the technical architecture of Immich Memories after the major refactor that split the codebase into 80+ files, all under 500 lines each.

For the LLM-optimized version with full module listings, see the root [ARCHITECTURE.md](../ARCHITECTURE.md).

## Build System

The **Makefile** is the single source of truth for all commands. CI, pre-commit hooks, and local development all use the same `make` targets. Run `make help` to see everything available.

Key targets:

```bash
make check       # Run all checks (lint + format + typecheck + file-length + complexity + test)
make ci          # Full CI-equivalent pipeline (all checks + dead-code)
make test        # Run tests
make complexity  # Check cyclomatic complexity (Xenon grade C)
make file-length # Verify all .py files are ≤500 lines
```

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   NiceGUI UI    │  │      CLI        │  │   REST API      │  │
│  │   (ui/app.py)   │  │  (cli/*.py)     │  │   (future)      │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
└───────────┼─────────────────────┼─────────────────────┼──────────┘
            │                     │                     │
            └─────────────────────┼─────────────────────┘
                                  │
┌─────────────────────────────────┼─────────────────────────────────┐
│                        Core Pipeline                               │
│  ┌──────────────────────────────┴──────────────────────────────┐  │
│  │                    Smart Pipeline                            │  │
│  │            (analysis/smart_pipeline.py + 4 mixins)          │  │
│  └──────────────────────────────┬──────────────────────────────┘  │
│                                 │                                  │
│  ┌──────────┐ ┌──────────┐ ┌───┴────┐ ┌──────────┐               │
│  │Clustering│→│Filtering │→│Analyzing│→│Refining  │               │
│  │ Phase 1  │ │ Phase 2  │ │Phase 3 │ │ Phase 4  │               │
│  └──────────┘ └──────────┘ └────────┘ └──────────┘               │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
┌───────────┼─────────────────────┼─────────────────────┼───────────┐
│           │     Analysis Layer  │                     │           │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐  │
│  │   Duplicates    │  │  Scene Scoring  │  │  LLM Content    │  │
│  │   Detection     │  │    & Scenes     │  │   Analysis      │  │
│  │ (duplicates.py) │  │  (scoring.py)   │  │(content_analyzer)│  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
┌───────────┼─────────────────────┼─────────────────────┼───────────┐
│           │   Processing Layer  │                     │           │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐  │
│  │  Video Clips    │  │    Assembly     │  │   Hardware      │  │
│  │   Extraction    │  │  & Transitions  │  │  Acceleration   │  │
│  │   (clips.py)    │  │(video_assembler)│  │  (hardware.py)  │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
            │                     │                     │
┌───────────┼─────────────────────┼─────────────────────┼───────────┐
│           │   External Services │                     │           │
│  ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐  │
│  │   Immich API    │  │   Ollama/OpenAI │  │     FFmpeg      │  │
│  │  (api/immich.py)│  │   (LLM APIs)    │  │  (subprocess)   │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

## Mixin Architecture

The 500-line file limit forced large classes to split into mixins. Each mixin lives in its own file and handles one responsibility. The main class inherits all mixins and serves as the public API.

### SmartPipeline (4 mixins)

The main orchestrator for the 4-phase video selection process:

| Mixin | File | Responsibility |
|-------|------|----------------|
| `AnalysisMixin` | `pipeline_analysis.py` | Clip analysis phase |
| `PreviewMixin` | `pipeline_preview.py` | Preview extraction |
| `RefinementMixin` | `pipeline_refinement.py` | Clip refinement |
| `ScalingMixin` | `pipeline_scaling.py` | Duration scaling |

### VideoAssembler (11 mixins)

Handles all FFmpeg-based video assembly:

| Mixin | File | Responsibility |
|-------|------|----------------|
| `AssemblerHelpersMixin` | `assembler_helpers.py` | Shared helper methods |
| `AssemblerProbingMixin` | `assembler_probing.py` | FFprobe duration probing |
| `AssemblerEncodingMixin` | `assembler_encoding.py` | Encoding/trimming |
| `AssemblerTransitionMixin` | `assembler_transitions.py` | Transition filter building |
| `AssemblerTransitionRenderMixin` | `assembler_transition_render.py` | Transition rendering |
| `AssemblerAudioMixin` | `assembler_audio.py` | Music/audio methods |
| `AssemblerConcatMixin` | `assembler_concat.py` | Concatenation/merge |
| `AssemblerStrategyMixin` | `assembler_strategies.py` | Assembly strategy selection |
| `AssemblerScalableMixin` | `assembler_scalable.py` | Scalable chunked assembly |
| `AssemblerBatchMixin` | `assembler_batch.py` | Batch merge/direct assembly |
| `AssemblerTitleMixin` | `assembler_titles.py` | Title screen integration |

### UnifiedSegmentAnalyzer (2 mixins)

Combines visual and audio analysis for segment selection:

| Mixin | File | Responsibility |
|-------|------|----------------|
| `SegmentScoringMixin` | `segment_scoring.py` | Segment scoring logic |
| `CandidateGenerationMixin` | `candidate_generation.py` | Candidate segment generation |

### SyncImmichClient (3 mixins)

REST API wrapper for communicating with the Immich server:

| Mixin | File | Responsibility |
|-------|------|----------------|
| `AssetMixin` | `client_asset.py` | Asset/video operations |
| `PersonMixin` | `client_person.py` | Person/face operations |
| `SearchMixin` | `client_search.py` | Search operations |

## Core Components

### 1. Smart Pipeline (`analysis/smart_pipeline.py`)

The main orchestrator runs 4 phases:

**Phase 1: Clustering** groups similar videos using perceptual hashing. Time-based clustering catches clips within 2 minutes of each other. Selects the best representative from each cluster.

**Phase 2: Filtering** applies HDR filter, prioritizes favorites, limits non-favorite ratio, and distributes clips across dates.

**Phase 3: Analyzing** downloads videos (cached), runs visual analysis (faces, motion, stability), optional LLM content analysis, and audio-aware boundary detection.

**Phase 4: Refining** does final clip selection with optimal segment extraction and date-distributed selection.

### 2. Unified Analyzer (`analysis/unified_analyzer.py`)

Combines visual and audio analysis for intelligent segment selection:

1. Detect visual scene boundaries (PySceneDetect)
2. Detect audio silence gaps (FFmpeg)
3. Merge into unified cut points with priority levels
4. Generate candidate segments
5. Score candidates (visual + optional LLM content)
6. Return best segment

**Scoring formula:**
```
total_score = visual_score * (1 - content_weight)
            + content_score * content_weight
            + cut_quality * 0.15
```

### 3. Content Analyzer (`analysis/content_analyzer.py`)

Optional LLM-powered content understanding. Extracts 2-3 frames per segment, sends to Ollama or OpenAI vision models, parses JSON response with fallback regex extraction. Returns description, emotion, and scores.

### 4. Duplicate Detection (`analysis/duplicates.py`)

Perceptual hashing finds similar videos. Builds a similarity graph based on hash distance and time proximity, then uses union-find to create clusters.

### 5. Video Assembly (`processing/video_assembler.py`)

Pure FFmpeg pipeline for combining clips with transitions. Supports cuts, crossfades, and smart transitions. Handles HDR conversion, resolution scaling, smart cropping, and audio normalization.

## Package Structure

```
src/immich_memories/
├── api/                    # Immich server communication (4 files)
├── analysis/               # Video analysis & clip selection (23 files)
├── processing/             # Video processing & assembly (22 files)
├── audio/                  # Audio processing & music (14 files)
├── titles/                 # Title screen generation (18 files)
├── cli/                    # CLI commands via Click (7 files)
├── ui/                     # NiceGUI web interface (11 files)
│   └── pages/              # Step-by-step wizard pages
├── tracking/               # Run history & telemetry (7 files)
├── cache/                  # Thumbnail, video, and analysis caching
├── config.py               # Re-export shim for config_loader + config_models
├── config_loader.py        # Config loading logic
├── config_models.py        # Config data models
├── timeperiod.py           # Date range utilities
├── security.py             # Input sanitization
├── i18n.py                 # Internationalization
└── preflight.py            # Dependency checks
```

Every `.py` file is under 500 lines. Large classes use mixins. Re-export shims (`assembly.py`, `mixer.py`, `config.py`) maintain backwards compatibility.

## Data Flow

```
Immich API → Fetch Videos → Thumbnails
    │
    ▼
Clustering → Similar videos grouped
    │
    ▼
Filtering → HDR, favorites, ratio limits
    │
    ▼
Analyzing → Download → Visual + Audio + LLM
    │
    ▼
Refining → Final selection, segments
    │
    ▼
Assembly → Transitions, music, export
```

## Configuration System

Priority order (lowest to highest):
1. Default values in code
2. `~/.immich-memories/config.yaml`
3. Environment variables (`IMMICH_MEMORIES_*`)
4. CLI arguments

## Hardware Acceleration

Auto-detects and uses available GPU hardware:
- NVIDIA NVENC (Linux, Windows)
- Apple VideoToolbox (macOS)
- Intel Quick Sync (Linux, Windows)
- VAAPI (AMD/Intel on Linux)

## Error Handling

Graceful degradation throughout:
- Scene detection fails: fall back to fixed segments
- LLM fails: use visual-only scoring
- Hardware acceleration fails: use software encoding
- Audio extraction fails: skip silence detection

## Caching Strategy

3 separate caches:
- **Thumbnail cache** (`cache/thumbnail_cache.py`): SQLite-backed, stores by asset ID
- **Video cache** (`cache/video_cache.py`): Downloads on-demand, LRU eviction
- **Analysis cache**: Session state, persists across UI refreshes

## Testing

Run tests with `make test`. Run the full CI pipeline locally with `make ci`.

Tests live in `tests/`, use pytest, and mock external services (Immich API, FFmpeg, LLM providers).
