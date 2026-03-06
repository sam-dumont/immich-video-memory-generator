# Architecture Overview

This document describes the technical architecture of Immich Memories.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   NiceGUI UI    │  │      CLI        │  │   REST API      │  │
│  │   (ui/app.py)   │  │   (cli.py)      │  │   (future)      │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
└───────────┼─────────────────────┼─────────────────────┼──────────┘
            │                     │                     │
            └─────────────────────┼─────────────────────┘
                                  │
┌─────────────────────────────────┼─────────────────────────────────┐
│                        Core Pipeline                               │
│  ┌──────────────────────────────┴──────────────────────────────┐  │
│  │                    Smart Pipeline                            │  │
│  │                (analysis/smart_pipeline.py)                  │  │
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
│  │   (clips.py)    │  │  (assembly.py)  │  │  (hardware.py)  │  │
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

## Core Components

### 1. Smart Pipeline (`analysis/smart_pipeline.py`)

The main orchestrator that runs the 4-phase video selection process:

```python
@dataclass
class PipelineConfig:
    target_clips: int = 120
    avg_clip_duration: float = 5.0
    hdr_only: bool = False
    prioritize_favorites: bool = True
    max_non_favorite_ratio: float = 0.25
    cluster_threshold: int = 14
```

**Phase 1: Clustering**
- Groups similar videos using perceptual hashing
- Time-based clustering for clips within 2 minutes
- Selects best representative from each cluster

**Phase 2: Filtering**
- Applies HDR filter if requested
- Prioritizes favorites
- Limits non-favorite ratio
- Distributes clips across dates

**Phase 3: Analyzing**
- Downloads videos (cached)
- Runs visual analysis (faces, motion, stability)
- Optional LLM content analysis
- Audio-aware boundary detection

**Phase 4: Refining**
- Final clip selection
- Optimal segment extraction
- Date-distributed selection

### 2. Unified Analyzer (`analysis/unified_analyzer.py`)

Combines visual and audio analysis for intelligent segment selection:

```python
class UnifiedSegmentAnalyzer:
    def analyze_video(self, video_path: Path) -> ScoredSegment:
        # 1. Detect visual scene boundaries
        # 2. Detect audio silence gaps
        # 3. Merge into unified cut points
        # 4. Generate candidate segments
        # 5. Score candidates (visual + content)
        # 6. Return best segment
```

**Cut Point Priority:**
- Priority 2: Both visual + audio boundary (ideal)
- Priority 1: Visual OR audio boundary
- Priority 0: Neither (fallback)

**Scoring Formula:**
```
total_score = visual_score * (1 - content_weight)
            + content_score * content_weight
            + cut_quality * 0.15
```

### 3. Content Analyzer (`analysis/content_analyzer.py`)

Optional LLM-powered content understanding:

```python
class OllamaContentAnalyzer(ContentAnalyzer):
    SINGLE_IMAGE_MODELS = {"moondream", "moondream2"}

    def analyze_segment(self, video_path, start, end):
        # Extract 2 frames (for Moondream) or 3 frames (other models)
        # Send to LLM with analysis prompt
        # Parse JSON response (with fallback regex extraction)
        # Return ContentAnalysis with description, emotion, scores
```

### 4. Duplicate Detection (`analysis/duplicates.py`)

Perceptual hashing for finding similar videos:

```python
def cluster_thumbnails(clips, thumbnail_cache, threshold=14):
    # Compute perceptual hash for each thumbnail
    # Build similarity graph based on:
    #   - Hash distance <= threshold, OR
    #   - Time proximity (< 2 min) AND hash distance <= threshold * 1.5
    # Union-find to create clusters
    # Select best representative from each cluster
```

### 5. Silence Detection (`analysis/silence_detection.py`)

Audio analysis for natural cut points:

```python
def detect_silence_gaps(video_path, threshold_db=-40.0, min_duration=0.2):
    # Extract audio with FFmpeg (handles iPhone spatial audio)
    # Analyze RMS energy in windows
    # Find gaps below threshold
    # Return list of (start, end) silence gaps
```

## Data Flow

### Video Selection Flow

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

### Analysis Flow (per video)

```
Video File
    │
    ├──► Scene Detection (PySceneDetect)
    │        │
    │        └──► Visual boundaries
    │
    ├──► Silence Detection (FFmpeg)
    │        │
    │        └──► Audio boundaries
    │
    └──► Merge Boundaries
             │
             ▼
         Cut Points (with priorities)
             │
             ▼
         Candidate Segments
             │
             ├──► Visual Scoring (faces, motion, stability)
             │
             └──► LLM Scoring (optional)
                      │
                      ▼
                  Best Segment
```

## Caching Strategy

### Thumbnail Cache (`cache/thumbnail_cache.py`)
- SQLite-backed storage
- Stores thumbnails by asset ID
- Separate preview and thumbnail sizes

### Video Cache (`cache/video_cache.py`)
- Downloads videos on-demand
- LRU eviction for disk space
- Supports downscaled analysis copies

### Analysis Cache
- Results stored in session state
- Persists across UI refreshes
- "Use Cached Analysis" button

## Configuration System

### Hierarchy (lowest to highest priority)
1. Default values in code
2. `~/.immich-memories/config.yaml`
3. Environment variables (`IMMICH_MEMORIES_*`)
4. CLI arguments

### Key Configuration Classes

```python
@dataclass
class AnalysisConfig:
    use_scene_detection: bool = True
    max_segment_duration: float = 10.0
    min_segment_duration: float = 1.5
    silence_threshold_db: float = -40.0
    min_silence_duration: float = 0.2

@dataclass
class ContentAnalysisConfig:
    enabled: bool = False
    weight: float = 0.2
    analyze_frames: int = 3
```

## Hardware Acceleration

### Detection (`processing/hardware.py`)

```python
def detect_hardware():
    # Check for NVIDIA (nvenc)
    # Check for Apple VideoToolbox
    # Check for Intel QSV
    # Check for VAAPI
    # Return HardwareCapabilities
```

### Usage

Hardware acceleration is used for:
- Video decoding (if supported)
- Video encoding (h264_nvenc, hevc_videotoolbox, etc.)
- Scaling (scale_cuda, etc.)

## Error Handling

### Graceful Degradation

- Scene detection fails → Fall back to fixed segments
- LLM fails → Use visual-only scoring
- Hardware accel fails → Use software encoding
- Audio extraction fails → Skip silence detection

### Progress Tracking (`analysis/progress.py`)

```python
class ProgressTracker:
    def start_phase(self, phase: PipelinePhase, total: int)
    def start_item(self, name: str, asset_id: str)
    def complete_item(self, asset_id: str, **kwargs)
    def get_status(self) -> dict  # For UI updates
```

## Testing Strategy

### Unit Tests
- Individual function testing
- Mock external services (Immich, FFmpeg, LLM)

### Integration Tests
- Pipeline flow testing
- Real video processing (small samples)

### Manual Testing
- UI interaction
- Various video formats
- Edge cases (no audio, very short, etc.)
