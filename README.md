# Immich Memories

[![CI](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sam-dumont/immich-video-memory-generator/graph/badge.svg)](https://codecov.io/gh/sam-dumont/immich-video-memory-generator)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/sam-dumont/immich-video-memory-generator/badge)](https://scorecard.dev/viewer/?uri=github.com/sam-dumont/immich-video-memory-generator)
[![Release](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml)
[![Python](https://img.shields.io/pypi/pyversions/immich-memories)](https://pypi.org/project/immich-memories/)
[![License](https://img.shields.io/github/license/sam-dumont/immich-video-memory-generator)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Docusaurus-blue)](https://sam-dumont.github.io/immich-video-memory-generator/)

**Create beautiful yearly video compilations from your [Immich](https://immich.app/) photo library.**

Immich Memories connects to your self-hosted Immich server, intelligently selects the best moments from your videos, and compiles them into shareable memory videos — perfect for year-end recaps or celebrating specific people in your life.

> **Full documentation**: [sam-dumont.github.io/immich-video-memory-generator](https://sam-dumont.github.io/immich-video-memory-generator/)

---

## Docker (recommended for self-hosters)

```bash
# 1. Download the compose file
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml

# 2. Set your Immich connection
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"

# 3. Start
docker compose up -d

# 4. Open http://localhost:8080
```

### Resource Usage

| State | RAM | CPU | Notes |
|-------|-----|-----|-------|
| Idle (UI only) | ~50MB | minimal | NiceGUI web server |
| Analyzing clips | 1-2GB | 1-2 cores | Downloads + scores video clips |
| Encoding video | 1-4GB | 2-4 cores | FFmpeg assembly, depends on resolution |

Default Docker limits: 2GB RAM, 2 CPUs. Increase for 4K output or large libraries (500+ videos).

### Supported Immich Versions

Tested with Immich v1.100+. Uses the `/api/` endpoint prefix.

## Quick Install

```bash
# One-liner (no clone needed)
uvx immich-memories --help

# Or clone and install
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync
```

## Quick Start

```bash
# 1. Configure
mkdir -p ~/.immich-memories
cat > ~/.immich-memories/config.yaml << EOF
immich:
  url: "https://photos.example.com"
  api_key: "your-api-key-here"
EOF

# 2. Launch the UI
immich-memories ui
# Opens at http://localhost:8080

# 3. Or use the CLI
immich-memories generate --year 2024 --person "John" --output ~/Videos/john_2024.mp4
```

## Key Features

- **Immich Integration** — Direct REST API connection with face recognition support
- **Smart Clip Selection** — Scene detection, interest scoring, duplicate filtering
- **Face-Aware Cropping** — Keeps faces centered when converting aspect ratios
- **Hardware Acceleration** — NVIDIA NVENC, Apple VideoToolbox, Intel QSV, AMD VAAPI
- **AI Music Generation** — ACE-Step or MusicGen with automatic mood detection
- **Audio Ducking** — Music lowers automatically during speech
- **Web UI + CLI** — 4-step wizard or headless automation
- **Docker & Kubernetes** — Containerized deployment with GPU support

## Documentation

See the [full documentation](https://sam-dumont.github.io/immich-video-memory-generator/) for:

- [Installation options](https://sam-dumont.github.io/immich-video-memory-generator/docs/installation/uv) (uv, pip, Docker, Kubernetes, Terraform)
- [UI Walkthrough](https://sam-dumont.github.io/immich-video-memory-generator/docs/ui-walkthrough/overview)
- [CLI Reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/cli/overview)
- [Configuration](https://sam-dumont.github.io/immich-video-memory-generator/docs/configuration/config-file)
- [Hardware Acceleration](https://sam-dumont.github.io/immich-video-memory-generator/docs/hardware/overview)
- [AI Music](https://sam-dumont.github.io/immich-video-memory-generator/docs/music/overview)
- [Guides](https://sam-dumont.github.io/immich-video-memory-generator/docs/guides/first-video)

## Development

```bash
make dev      # Install all dependencies
make check    # Run all checks (lint, format, typecheck, tests)
make ci       # Full CI pipeline
make help     # Show all available targets
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Built with AI

> This entire codebase was written with AI (Claude) as an experiment in building complex
> software cleanly with AI assistance. 1,100+ tests, strict quality gates, the works.
> See [DISCLAIMER.md](DISCLAIMER.md) for the full story.

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Made with ❤️ for the Immich community**
