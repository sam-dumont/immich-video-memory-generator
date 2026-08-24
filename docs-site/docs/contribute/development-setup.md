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
| `make ci` | Full CI pipeline (the 16 local gates, plus the unit tests) |
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

### If diff-cover fails on your PR

Every PR needs 80% coverage on the lines it changes. Before checking that, CI runs the FFmpeg-only integration suites covering the paths your diff touches, and only those, then merges their coverage into the diff-cover run. So code reachable only through FFmpeg is covered for you: you do not need to write unit tests for it.

To reproduce locally exactly what CI will see:

```bash
make integration-coverage-for-diff   # runs only the suites your diff touches
make diff-cover-local                # merges them with unit coverage, same as CI
```

If diff-cover still fails after that, the uncovered lines are not reachable from an integration suite and do need unit tests. Subprocess boundaries can be stubbed rather than run for real: `tests/test_ffmpeg_pipe.py` shows the pattern.

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
