---
sidebar_label: "Development Setup"
---

# Development Setup

The full contribution guidelines are in [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md).

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

`make dev-test` is `uv sync --extra dev --locked`: the dev tools (pytest, ruff, mypy and the other CI gates) and nothing else. That is the fast path, no torch and no CUDA, and it is what the CI test jobs install. There is no `gpu` extra to add: the GPU title kernels are a base dependency wherever they publish a wheel. Run it before any other make target.

Other install targets, when you need them:

| Target | Installs | When |
|--------|----------|------|
| `make dev-ci` | dev tools only | Lint/typecheck-only work. Identical to `dev-test` today |
| `make dev-test` | dev tools only | Default for contributors (what CI tests with) |
| `make dev-mac` | dev + `all-mac` (Apple Vision, Metal, the editorial stack) | Apple Silicon, full feature set |
| `make dev` | every declared extra (torch, demucs, editorial), slow | Only if you work across all optional backends |

## Check the install

```bash
make check
```

This runs lint, format check, type check, file length gate, complexity gate, and all unit tests. It is the fast subset: it skips cognitive complexity, dead code, refurb, dep-check, arch-check, critique, the drift gates and every security scan. `make ci` runs those. If `make check` passes, your setup is correct.

Both `make check` and `make ci` depend on `ensure-dev`, which syncs every extra, so a run of either turns a `make dev-test` environment into a `make dev` one.

## Key commands

| Command | What it does |
|---------|-------------|
| `make test` | Unit tests |
| `make lint` | Ruff linter |
| `make format` | Auto-format code |
| `make typecheck` | mypy type checking |
| `make ci` | Full CI pipeline (19 local gates, plus the unit tests) |
| `make critique` | AI smell audit |
| `make test-integration` | Integration tests (needs FFmpeg + Immich) |

The **Makefile** is the single source of truth. Never run `ruff`, `pytest`, or `mypy` directly: the make targets match what CI runs, so local results are consistent.

## Before submitting a PR

```bash
make ci
```

If `make ci` passes locally, CI will pass too. Use [conventional commit](https://www.conventionalcommits.org/) messages: `feat(scope): description`, `fix(scope): description`, etc.

## Testing

`make test` is the unit suite, `make test-integration` the ones that need FFmpeg and Immich. The
tiers, what each needs, and what to do when diff-cover fails on your PR are in the
[Testing guide](testing.md).

## Private terms gate

`make privacy-gate` (and two pre-commit hooks: one on the staged diff, one on the commit message) blocks owner-defined private terms (family names, birth dates, fine-grained GPS coordinates, anything the maintainer doesn't want landing in a diff, commit message, or PR title/body) using `scripts/private_terms_gate.py`.

The denylist itself never lives in this repo. It resolves from, in order: `--terms-file`, an env var named by `--terms-env` (how CI reads it from the `PRIVATE_TERMS` repository secret), `$IMMICH_MEMORIES_PRIVATE_TERMS` (a path), or `~/.config/immich-memories/private-terms.txt`. One term per line; `#` comments and blank lines are ignored; a line starting with `re:` is a regex. If none of those resolve to anything, the gate prints a notice and exits clean: most contributors have no denylist configured, and that isn't a failure.

Every reported match is masked to its first character, so a hit report never contains the term it found. Forks never see the `PRIVATE_TERMS` secret, so the PR-automation job that scans title/body/diff skips there too.

## Project structure

```
src/immich_memories/
  api/          # Immich API client
  analysis/     # Story-first selection (the editorial route)
  store/        # The annotation store: every banked fact and reading
  triage/       # The pinned ONNX encoder and its six context heads
  people/       # The people graph and the companion file
  photos/       # Photo-to-video animation
  processing/   # Video assembly (FFmpeg)
  titles/       # Title screens, map fly-overs
  audio/        # Music generation, audio ducking
  ui/           # NiceGUI web interface
  cli/          # Click commands
  cache/        # Preview, video and run-history caching
  tracking/     # Run history
  operations/   # Lifecycle phases, storage report
  planning/     # Auto-duration planning
  scheduling/   # Cron-based generation
  automation/   # auto suggest/run
  memory_types/ # Preset system
```

See [ARCHITECTURE.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/ARCHITECTURE.md) for the full module map with class relationships.
