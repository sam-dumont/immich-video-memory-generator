# Immich Memories

[![CI](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml)
[![Release](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Create beautiful yearly video compilations from your [Immich](https://immich.app/) photo library.**

Immich Memories connects to your self-hosted Immich server, intelligently selects the best moments from your videos, and compiles them into shareable memory videos - perfect for year-end recaps or celebrating special people in your life.

---

## ⚠️ Important Disclaimer

> **THIS SOFTWARE WAS GENERATED PRIMARILY BY AI (Claude by Anthropic)**
>
> This project was developed with extensive assistance from large language models. While functional, it comes with **even fewer guarantees than typical open-source software**:
>
> - **No Warranty**: The code may contain bugs, inefficiencies, or unexpected behaviors
> - **Use at Your Own Risk**: Always backup your data before use
> - **Review Before Production**: We recommend code review before deploying in production environments
> - **Community Maintained**: Contributions to improve code quality are highly encouraged
>
> By using this software, you acknowledge these limitations. See [DISCLAIMER.md](DISCLAIMER.md) for full details.

---

## What It Does

1. **Connects to Immich**: Fetches your video library via Immich's REST API
2. **Filters by Person/Year**: Use Immich's face recognition to create compilations for specific people
3. **Finds Duplicates**: Automatically detects similar videos and ranks them by quality
4. **Selects Best Moments**: Uses scene detection and interest scoring to find the most engaging clips
5. **Smart Crops for Faces**: Keeps faces centered when converting between aspect ratios
6. **Compiles Videos**: Assembles clips with transitions and optional music
7. **Hardware Accelerated**: Uses GPU encoding (NVIDIA, Apple, Intel, AMD) for fast processing

## Features

- **Immich Integration**: Direct connection to your Immich server via REST API
- **Smart Person Selection**: Compile videos featuring specific people using Immich's face recognition
- **Flexible Time Periods**: Calendar years, birthday-based years, custom date ranges, or duration-based
- **Duplicate Detection**: Perceptual hashing to find and rank similar videos by quality
- **Scene Detection**: Natural scene boundaries using PySceneDetect (enabled by default)
- **Intelligent Moment Selection**: Multi-factor interest scoring (faces, motion, stability, content)
- **LLM Content Analysis**: Optional AI-powered content understanding via Ollama or OpenAI
- **Fast Analysis**: Videos downscaled to 480p for 3-5x faster analysis while maintaining accuracy
- **Memory Optimized**: Aggressive garbage collection and on-demand thumbnail loading prevents OOM
- **Resume Support**: Previously analyzed clips are cached and shown on restart
- **Review Mode**: Review and refine selected clips after AI analysis with deselection support
- **Smooth Transitions**: Crossfade transitions with configurable buffer footage
- **Automatic Music**: AI-powered mood detection + royalty-free music from Pixabay
- **Smart Audio Ducking**: Music automatically lowers when speech/sounds are detected
- **Flexible Output**: Portrait (9:16), Landscape (16:9), and Square (1:1) formats
- **Smart Cropping**: Face-aware cropping keeps subjects centered
- **Hardware Acceleration**: GPU-accelerated encoding with NVIDIA NVENC, Apple VideoToolbox, Intel QSV, VAAPI
- **Apple Silicon Optimized**: Vision framework for Neural Engine-accelerated face detection
- **Interactive UI**: NiceGUI web interface for easy video curation
- **CLI Support**: Headless operation for automation and scripting
- **Docker Ready**: Containerized deployment alongside Immich

## Installation

### Prerequisites

- **Python 3.12+** (3.13 recommended)
- **FFmpeg** (for video processing)
- **Immich server** with API access

### Quick Install with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a modern Python package manager that's 10-100x faster than pip.

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync

# Run the app
uv run immich-memories --help
```

### One-liner Install (uv)

```bash
# Install and run directly without cloning
uvx immich-memories --help
```

### Using pip

```bash
pip install immich-memories
```

### From Source (pip)

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
pip install -e .
```

### Platform-Specific Extras

#### Mac Users (Recommended for Apple Silicon)

```bash
# With uv
uv sync --extra mac

# With pip
pip install immich-memories[mac]
```

Enables:
- **Apple Vision Framework**: GPU-accelerated face detection via Neural Engine
- **VideoToolbox**: Hardware H.264/H.265/ProRes encoding
- **Metal**: GPU-accelerated image processing

#### Face Recognition (requires dlib)

```bash
# With uv
uv sync --extra face

# With pip
pip install immich-memories[face]
```

#### All Features

```bash
# With uv
uv sync --all-extras

# With pip
pip install immich-memories[all]
```

## Quick Start

### 1. Configure Immich Connection

```bash
# Option A: Config file
mkdir -p ~/.immich-memories
cat > ~/.immich-memories/config.yaml << EOF
immich:
  url: "https://photos.example.com"
  api_key: "your-api-key-here"
EOF

# Option B: Environment variables
export IMMICH_URL="https://photos.example.com"
export IMMICH_API_KEY="your-api-key-here"
```

### 2. Launch the UI

```bash
immich-memories ui
# Opens at http://localhost:8080
```

### 3. Or Use the CLI

```bash
# Generate a video compilation for a calendar year
immich-memories generate \
  --year 2024 \
  --person "John" \
  --duration 10 \
  --orientation landscape \
  --output ~/Videos/john_2024.mp4

# Generate using birthday-based year (Feb 7, 2024 to Feb 6, 2025)
immich-memories generate \
  --birthday 2024-02-07 \
  --person "John" \
  --output ~/Videos/john_birthday_year.mp4

# Generate for a specific date range
immich-memories generate \
  --start 2024-06-01 \
  --end 2024-08-31 \
  --person "John" \
  --output ~/Videos/john_summer.mp4

# Generate for a period from start date (6 months, 1 year, etc.)
immich-memories generate \
  --start 2024-01-01 \
  --period 6m \
  --output ~/Videos/first_half_2024.mp4

# Check hardware acceleration
immich-memories hardware

# Analyze videos (cache metadata)
immich-memories analyze --year 2024
```

## Usage Guide

### Interactive UI (Recommended)

The NiceGUI web interface provides a 5-step wizard:

1. **Configuration**: Connect to Immich, select year and person
2. **Clip Review**: Browse videos with duplicate grouping, select clips
3. **Moment Refinement**: Fine-tune in/out points for each clip
4. **Generation Options**: Choose music, transitions, output format
5. **Preview & Export**: Generate and download your compilation

### Keyboard Shortcuts (Moment Refinement)

| Key | Action |
|-----|--------|
| `J` / `K` / `L` | Rewind / Pause / Forward |
| `I` | Set in-point |
| `O` | Set out-point |
| `Space` | Play/Pause |
| `←` / `→` | Frame step |

### CLI Commands

```bash
immich-memories --help          # Show all commands
immich-memories ui              # Launch web UI
immich-memories generate        # Create compilation
immich-memories analyze         # Analyze and cache video metadata
immich-memories hardware        # Show hardware acceleration info
immich-memories people          # List recognized people
immich-memories years           # List years with videos
immich-memories config          # Show/edit configuration
immich-memories export-project  # Export project for external editing
immich-memories music search    # Search for royalty-free music
immich-memories music analyze   # Analyze video mood for music selection
immich-memories music add       # Add background music to a video
```

### Time Period Options

The `generate` command supports flexible time period selection:

| Option | Description | Example |
|--------|-------------|---------|
| `--year` | Calendar year (Jan 1 - Dec 31) | `--year 2024` |
| `--birthday` | Birthday-based year | `--birthday 2024-02-07` |
| `--start` + `--end` | Custom date range | `--start 2024-06-01 --end 2024-08-31` |
| `--start` + `--period` | Duration from start | `--start 2024-01-01 --period 6m` |

Period format: Number + unit (`d`=days, `w`=weeks, `m`=months, `y`=years). Examples: `6m`, `1y`, `2w`, `90d`

### Music Commands

```bash
# Search for music by mood/genre
immich-memories music search --mood happy --genre acoustic --limit 5

# Analyze a video's mood
immich-memories music analyze ~/Videos/my_video.mp4

# Add background music to a video (auto-selects music based on video mood)
immich-memories music add ~/Videos/input.mp4 ~/Videos/output.mp4

# Add specific music file with custom settings
immich-memories music add ~/Videos/input.mp4 ~/Videos/output.mp4 \
  --music ~/Music/song.mp3 \
  --volume -8 \
  --fade-in 3 \
  --fade-out 4
```

## Configuration

Configuration file: `~/.immich-memories/config.yaml`

```yaml
immich:
  url: "https://photos.example.com"
  api_key: "${IMMICH_API_KEY}"  # Environment variable substitution

defaults:
  target_duration_minutes: 10
  output_orientation: "landscape"  # landscape, portrait, square
  scale_mode: "smart_crop"         # fit, fill, smart_crop
  transition: "crossfade"
  transition_duration: 0.5
  transition_buffer: 0.5           # Extra footage for smooth fades

analysis:
  scene_threshold: 27.0
  min_scene_duration: 1.0
  duplicate_hash_threshold: 8
  enable_downscaling: true         # Downscale videos for faster analysis
  analysis_resolution: 480         # Resolution for analysis (480p = ~3-5x faster)

  # Scene detection (enabled by default for natural boundaries)
  use_scene_detection: true        # Use PySceneDetect for natural boundaries
  max_segment_duration: 10.0       # Long scenes are subdivided (seconds)
  min_segment_duration: 1.5        # Filter out very short segments

output:
  directory: "~/Videos/Memories"
  format: "mp4"
  resolution: "1080p"  # 720p, 1080p, 4k
  codec: "h264"        # h264, h265, prores
  crf: 18

hardware:
  enabled: true
  backend: "auto"            # auto, nvidia, apple, vaapi, qsv, none
  encoder_preset: "balanced" # fast, balanced, quality
  gpu_decode: true
  gpu_analysis: true

audio:
  auto_music: false          # Auto-select music based on video mood
  music_source: "pixabay"    # pixabay or local
  local_music_dir: "~/Music/Memories"

  # LLM for mood analysis (Ollama preferred, OpenAI fallback)
  ollama_url: "http://localhost:11434"
  ollama_model: "llava"
  openai_api_key: "${OPENAI_API_KEY}"
  openai_model: "gpt-4o-mini"

  # Audio ducking (lowers music when speech detected)
  ducking_threshold: 0.02    # Sensitivity (lower = more sensitive)
  ducking_ratio: 6.0         # How much to lower music
  music_volume_db: -6.0      # Base music volume
  fade_in_seconds: 2.0
  fade_out_seconds: 3.0

# LLM-based content analysis for intelligent scoring (optional)
content_analysis:
  enabled: false             # Disabled by default (adds processing time)
  weight: 0.2                # Weight in scoring (0-1, 20% of total score)
  provider: "auto"           # auto, ollama, openai (auto = try Ollama first)

  # Ollama settings (local, privacy-friendly)
  ollama_url: "http://localhost:11434"
  ollama_model: "llava"      # Vision model (llava, bakllava, llava-llama3)

  # OpenAI settings (fallback)
  openai_api_key: "${OPENAI_API_KEY}"
  openai_model: "gpt-4o-mini"

  # Analysis parameters
  analyze_frames: 3          # Frames per segment (1-4)
  min_confidence: 0.5        # Minimum confidence to use score
```

### Environment Variables

All configuration options can be set via environment variables using the pattern:
`IMMICH_MEMORIES_<section>__<field>` (double underscore for nesting)

```bash
# Core settings
export IMMICH_MEMORIES_IMMICH__URL="https://photos.example.com"
export IMMICH_MEMORIES_IMMICH__API_KEY="your-api-key"

# Analysis settings
export IMMICH_MEMORIES_ANALYSIS__USE_SCENE_DETECTION="true"
export IMMICH_MEMORIES_ANALYSIS__ENABLE_DOWNSCALING="true"

# Content analysis (LLM)
export IMMICH_MEMORIES_CONTENT_ANALYSIS__ENABLED="true"
export IMMICH_MEMORIES_CONTENT_ANALYSIS__PROVIDER="ollama"
export IMMICH_MEMORIES_CONTENT_ANALYSIS__OLLAMA_URL="http://localhost:11434"
export IMMICH_MEMORIES_CONTENT_ANALYSIS__OLLAMA_MODEL="llava"
export IMMICH_MEMORIES_CONTENT_ANALYSIS__OPENAI_API_KEY="sk-..."

# Hardware acceleration
export IMMICH_MEMORIES_HARDWARE__BACKEND="nvidia"
export IMMICH_MEMORIES_HARDWARE__GPU_ANALYSIS="true"

# Output settings
export IMMICH_MEMORIES_OUTPUT__RESOLUTION="1080p"
export IMMICH_MEMORIES_OUTPUT__CODEC="h265"
```

## Hardware Acceleration

Immich Memories automatically detects and uses available GPU hardware.

### Supported Backends

| Backend | Platform | Encoder | Notes |
|---------|----------|---------|-------|
| **NVIDIA NVENC** | Linux, Windows | h264_nvenc, hevc_nvenc | Requires CUDA drivers |
| **Apple VideoToolbox** | macOS | h264_videotoolbox, hevc_videotoolbox | Built-in |
| **Intel Quick Sync** | Linux, Windows | h264_qsv, hevc_qsv | Intel GPU required |
| **VAAPI** | Linux | h264_vaapi, hevc_vaapi | AMD/Intel on Linux |

### Platform Feature Matrix

| Feature | NVIDIA | Apple Silicon | Intel QSV | AMD VAAPI |
|---------|--------|---------------|-----------|-----------|
| Video Encode | ✅ NVENC | ✅ VideoToolbox | ✅ QSV | ✅ VAAPI |
| Video Decode | ✅ NVDEC | ✅ VideoToolbox | ✅ QSV | ✅ VAAPI |
| GPU Scaling | ✅ scale_cuda | ⚠️ Software | ✅ scale_qsv | ✅ scale_vaapi |
| Face Detection | ✅ OpenCV CUDA | ✅ Vision Framework | ❌ CPU | ❌ CPU |

### Apple Silicon Benefits

- Neural Engine accelerates face detection (~10x faster than OpenCV)
- Unified memory eliminates CPU/GPU transfer overhead
- VideoToolbox encoding is 5-10x faster than software
- All M1/M2/M3/M4 chips fully supported

```bash
# Check your hardware capabilities
immich-memories hardware
```

## Docker

```bash
cd docker
docker-compose up -d

# Access UI at http://localhost:8080
```

## Kubernetes Deployment

Deploy to Kubernetes with NVIDIA GPU support. See [`deploy/`](deploy/) for complete examples.

### Quick Start with kubectl

```bash
cd deploy/kubernetes

# Edit secrets with your Immich credentials
vim secret.yaml

# Deploy all resources
kubectl apply -k .

# Access the UI
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

### Terraform Module

```bash
cd deploy/terraform/examples/basic

# Configure your settings
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

# Deploy
terraform init
terraform apply
```

### NVIDIA GPU Support

The deployment includes:

- **RuntimeClass**: Uses `nvidia` runtime for GPU access
- **Resource Limits**: Requests `nvidia.com/gpu: 1`
- **Node Selection**: Targets nodes with `nvidia.com/gpu.present=true`
- **Environment Variables**: Sets `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`

Prerequisites:
1. [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/overview.html) installed
2. NVIDIA RuntimeClass configured
3. GPU nodes labeled appropriately

### Batch Processing

Run one-off generation jobs:

```bash
# Edit job parameters
vim deploy/kubernetes/job.yaml

# Run the job
kubectl apply -f deploy/kubernetes/job.yaml

# Watch progress
kubectl logs -n immich-memories -f job/immich-memories-generate
```

## Development

### Setup

```bash
# Install uv and just
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install just  # or: cargo install just

# Setup development environment
just setup

# Run all checks
just check
```

### Common Commands

```bash
just test          # Run tests
just lint          # Lint code
just fmt           # Format code
just typecheck     # Type checking
just run --help    # Run CLI
just ui            # Launch UI
just build         # Build package
just clean         # Clean build artifacts
```

### Manual Setup

```bash
# With uv
uv sync --all-extras
uv run pytest
uv run mypy src/
uv run ruff check src/

# With pip
pip install -e ".[dev]"
pytest
mypy src/
ruff check src/
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Quick Contribution Guide

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run checks (`just check`)
5. Commit (`git commit -m 'Add amazing feature'`)
6. Push (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Requirements

- Python 3.12+ (3.13 recommended)
- FFmpeg (video processing)
- Immich server with API access

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Immich](https://immich.app/) - Self-hosted photo and video backup
- [MoviePy](https://zulko.github.io/moviepy/) - Video editing with Python
- [PySceneDetect](https://scenedetect.com/) - Scene detection library
- [uv](https://github.com/astral-sh/uv) - Fast Python package manager
- [Claude](https://anthropic.com/claude) - AI assistance in initial development

---

**Made with ❤️ for the Immich community**
