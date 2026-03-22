---
sidebar_label: "Development Setup"
---

# Development Setup

Get the project running locally for development. The full contribution guidelines are in [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md).

## Prerequisites

- **Python 3.11+**
- **FFmpeg** (for video processing tests)
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **GNU Make**

## Clone and install

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev
```

`make dev` installs all development dependencies (pytest, ruff, mypy, and the 17 CI tools) into the project virtualenv. Run it before any other make target.

## Verify everything works

```bash
make check
```

This runs lint, format check, type check, file length gate, complexity gate, and all unit tests. If it passes, your setup is correct.

## Key commands

| Command | What it does |
|---------|-------------|
| `make test` | Unit tests |
| `make lint` | Ruff linter |
| `make format` | Auto-format code |
| `make typecheck` | mypy type checking |
| `make ci` | Full CI pipeline (all 17 gates) |
| `make critique` | AI smell audit |
| `make test-integration` | Integration tests (needs FFmpeg + Immich) |

The **Makefile** is the single source of truth. Never run `ruff`, `pytest`, or `mypy` directly: the make targets match what CI runs, so local results are consistent.

## Before submitting a PR

```bash
make ci
```

If `make ci` passes locally, CI will pass too. Use [conventional commit](https://www.conventionalcommits.org/) messages: `feat(scope): description`, `fix(scope): description`, etc.

## Testing tiers

**Unit tests** (`make test`): pure logic, no external dependencies. Run in CI on every PR.

**Integration tests** (`make test-integration`): real FFmpeg assembly, real Immich API reads. Run locally because they need your Immich server and FFmpeg installed. They skip gracefully if services aren't available.

If CI's diff-cover fails because changed lines aren't covered by unit tests, run integration tests locally and commit the coverage XMLs:

```bash
make test-integration
git add tests/*-coverage.xml
```

## Project structure

```
src/immich_memories/
  api/          # Immich API client
  analysis/     # Video analysis, scoring, clip selection
  processing/   # Video assembly (FFmpeg)
  titles/       # Title screens, maps, globe animation
  audio/        # Music generation, audio ducking
  ui/           # NiceGUI web interface
  cache/        # Analysis and video caching
  tracking/     # Run history
  scheduling/   # Cron-based generation
  memory_types/ # Preset system
```

See [ARCHITECTURE.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/ARCHITECTURE.md) for the full module map with class relationships.
