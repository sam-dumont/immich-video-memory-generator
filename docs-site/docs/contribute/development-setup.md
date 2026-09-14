---
sidebar_label: "Development Setup"
---

# Development Setup

You need Python 3.11+, FFmpeg, [uv](https://docs.astral.sh/uv/), Git and GNU Make.
Some media fixtures use Git LFS. Install it and run `git lfs pull` if the checkout contains
pointer files instead of media.

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev
make check
```

`make dev` installs the declared extras and development tools. It can download large
packages, including Demucs dependencies. Model weights and the separately installed
ACE-Step runtime are additional setup steps when you work on those features.

The lighter install targets are useful for matching a CI environment:

| Target | Installs |
|---|---|
| `make dev-ci` | Base package and dev extra from `uv.lock` |
| `make dev-test` | The same locked base and dev environment used by CI's main test matrix |
| `make dev-mac` | `all-mac` and dev extras, including the macOS frameworks |
| `make dev` | All declared extras |

Quadrants title kernels are a base dependency on supported Python/platform combinations.
There is no separate `gpu` extra. The `editorial` extra uses ONNX Runtime; Demucs is the
optional backend that brings PyTorch.

`make check` and `make ci` both run `ensure-dev`, which syncs all extras. Starting with
`make dev-test` does not keep those aggregate checks in a minimal environment.

## Checks while working

| Command | What it does |
|---|---|
| `make test` | Default test suite; some tests run FFmpeg |
| `make test-one T=tests/test_config.py` | One file or test node |
| `make lint` | Ruff lint |
| `make format` | Apply Ruff formatting |
| `make typecheck` | mypy |
| `make check` | Lint, format check, types, file length, cyclomatic complexity and tests |
| `make ci` | Broader local quality, security, documentation and test gates |
| `make test-integration-assembly` | Real FFmpeg assembly tests |
| `make docs-build` | Build the documentation site |

Use the Makefile targets instead of running pytest, Ruff or mypy directly. `make help`
lists the entry points. See [Testing](./testing.md) for service requirements, focused
integration suites and coverage.

Before requesting review, run `make ci` and the checks needed by your change. A local pass
is evidence for your environment; GitHub Actions still needs to pass its platform matrix,
builds and other jobs. Contribution scope and PR expectations are in
[CONTRIBUTING.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/CONTRIBUTING.md).

## Private terms gate

`make privacy-gate` scans for owner-defined private terms. Pre-commit hooks also scan the
staged diff and commit message; PR automation can scan the title, body and diff.

The term list stays outside this repository. `scripts/private_terms_gate.py` resolves it
from `--terms-file`, `--terms-env`, the path in `IMMICH_MEMORIES_PRIVATE_TERMS`, or
`~/.config/immich-memories/private-terms.txt`, in that order. Use one term per line;
blank lines and `#` comments are ignored, and `re:` starts a regular expression.

With no list configured, the gate prints a notice and exits successfully. An explicitly
requested unreadable file is an error. Reports mask matched terms. Forks do not receive
the repository's `PRIVATE_TERMS` secret, so that secret-backed PR scan is skipped there.

This checks the configured terms. It cannot recognize every private name, image or detail
in a contribution.

## Code map

The main packages are `api/` for Immich, `analysis/` for editorial selection,
`photos/`, `processing/` and `titles/` for rendering, `audio/` for soundtracks, and
`ui/` and `cli/` for the interfaces. `automation/` selects unattended requests;
`memory_types/` defines their presets. `store/`, `cache/`, `operations/` and `tracking/`
hold facts, media caches, attempt state and run history.

The separate inference service lives under `services/inference/`. See
[Architecture](../reference/architecture.md) for the active boundaries.
