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
make dev-test
```

`make dev-test` installs the dev tools (pytest, ruff, mypy and the other CI gates) plus the `gpu` and `speech` extras — the same set the CI test jobs use. It is the fast path: no torch, no CUDA. Run it before any other make target.

Other install targets, when you need them:

| Target | Installs | When |
|--------|----------|------|
| `make dev-ci` | dev tools only | Lint/typecheck-only work |
| `make dev-test` | dev + `gpu` + `speech` | Default for contributors (what CI tests with) |
| `make dev-mac` | dev + `all-mac` (Apple Vision, Metal, ACE-Step MLX) | Apple Silicon, full feature set |
| `make dev` | every extra (torch, ACE-Step, demucs — slow) | Only if you work across all optional backends |

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
| `make ci` | Full CI pipeline (the 15 local gates) |
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

**Integration tests** (`make test-integration`, or one suite such as `make test-integration-assembly`): real FFmpeg assembly, real Immich API reads. They live in per-suite folders under `tests/integration/` (`assembly`, `audio`, `audio_mixing`, `auth`, `automation`, `cli`, `live_photos`, `photos`, `pipeline`, `processing`, `titles`) and skip gracefully if a service isn't available. They run locally and on a self-hosted Linux GPU runner, which uploads its coverage to Codecov under the `integration-linux` flag. The per-suite coverage XMLs they write under `tests/` are gitignored — do not try to commit them.

If CI's diff-cover fails because changed lines aren't covered by unit tests, add unit tests for those lines; the GPU runner's integration coverage is merged on Codecov but does not feed diff-cover.

## Project structure

```
src/immich_memories/
  api/          # Immich API client
  analysis/     # Video analysis, scoring, clip selection
  speech/       # VAD + transcription for cut placement
  photos/       # Photo-to-video animation
  processing/   # Video assembly (FFmpeg)
  titles/       # Title screens, map fly-overs
  audio/        # Music generation, audio ducking
  ui/           # NiceGUI web interface
  cli/          # Click commands
  cache/        # Analysis and video caching
  tracking/     # Run history
  operations/   # Lifecycle phases, storage report
  planning/     # Auto-duration planning
  scheduling/   # Cron-based generation
  automation/   # auto suggest/run
  memory_types/ # Preset system
```

See [ARCHITECTURE.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/ARCHITECTURE.md) for the full module map with class relationships.
