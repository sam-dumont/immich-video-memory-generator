---
sidebar_label: "Development Setup"
---

# Development setup

The full contribution guidelines are in [CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md).

You need Python 3.11+, FFmpeg, [uv](https://docs.astral.sh/uv/) and GNU Make.

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev-test
```

`make dev-test` is `uv sync --extra dev --locked`: the dev tools (pytest, ruff, mypy and the other
CI gates) and nothing else. No torch, no CUDA, and it is what the CI test jobs install. There is no
`gpu` extra to add: the GPU title kernels are a base dependency wherever they publish a wheel. Run
it before any other make target.

| Target | Installs | When |
|--------|----------|------|
| `make dev-test` | dev tools only | Default for contributors (what CI tests with) |
| `make dev-ci` | dev tools only | Identical to `dev-test` today |
| `make dev-mac` | dev + `all-mac` (Apple Vision, Metal, the editorial stack) | Apple Silicon, full feature set |
| `make dev` | every declared extra (torch, demucs, editorial), slow | Only if you work across all optional backends |

## Check the install

`make check` runs lint, format check, type check, the file length and complexity gates, and the
unit tests. If it passes, your setup is correct. `make ci` adds everything else and is what you run
before opening a PR: if it passes locally, CI will pass too. Both depend on `ensure-dev`, which
syncs every extra, so a run of either turns a `make dev-test` environment into a `make dev` one.

`make help` lists every target. Never run `ruff`, `pytest` or `mypy` directly: the make targets
match what CI runs, so local results are consistent. Use
[conventional commit](https://www.conventionalcommits.org/) messages.

The test tiers, what each needs, and what to do when diff-cover fails on your PR are in the
[Testing guide](testing.md).

## Private terms gate

`make privacy-gate` blocks owner-defined private terms (family names, birth dates, GPS
coordinates) from diffs, commit messages and PR titles. Two pre-commit hooks run it as well.

The denylist never lives in this repo. It resolves from, in order: `--terms-file`, an env var named
by `--terms-env` (how CI reads the `PRIVATE_TERMS` secret), `$IMMICH_MEMORIES_PRIVATE_TERMS` (a
path), or `~/.config/immich-memories/private-terms.txt`. One term per line, `#` comments ignored, a
`re:` prefix for a regex. With none of those configured the gate prints a notice and exits clean,
which is the normal case for a contributor. Matches are masked to their first character, so a hit
report never contains the term it found.

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

[ARCHITECTURE.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/ARCHITECTURE.md)
has the full module map with class relationships.
