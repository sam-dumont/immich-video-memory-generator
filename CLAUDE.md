# Claude Code Project Instructions

## Quick Start

1. **Install dev dependencies first**: `make dev`
2. Read `ARCHITECTURE.md` — it maps the full codebase structure, key classes,
   data flow, and mixin architecture. This avoids needing to explore the repo.

> **Important**: Always run `make dev` before running any other make target.
> This installs all dev tools (pytest, ruff, mypy, etc.) into the project venv.
> Without it, quality checks will fail with import errors.

## Commands

All commands are available via the **Makefile**. **Always use `make` targets** —
they match what CI runs, so local results are consistent with CI. Never run raw
`ruff`, `pytest`, `mypy`, `xenon`, etc. directly — use the Makefile.

```bash
# Install dev dependencies
make dev

# Run tests
make test

# Lint (ruff check)
make lint

# Auto-fix lint issues
make lint-fix

# Format code
make format

# Format check (no changes)
make format-check

# Type check (mypy)
make typecheck

# File length gate (≤500 lines per .py file)
make file-length

# Complexity gate (Xenon grade C)
make complexity

# Dead code detection (Vulture)
make dead-code

# Security lint (Bandit)
make security-lint

# Commit message lint (conventional commits)
make commitlint

# Dependency vulnerability audit
make pip-audit

# Run ALL checks (lint + format + typecheck + file-length + complexity + test)
make check

# Full CI pipeline (check + dead-code + security-lint)
make ci

# Pre-commit hooks (runs all local hooks: lint, format, mypy, gitleaks, commitizen, file-length, complexity, dead-code, security-lint)
make pre-commit
```

## Rules

### Code Quality Gates (enforced in CI)

- **Lint**: ruff check must pass (`make lint`)
- **Format**: ruff format must pass (`make format-check`)
- **Type check**: mypy must pass (`make typecheck`)
- **Max file length**: 500 lines per `.py` file (`make file-length`)
- **Max complexity**: Xenon grade C — ≤20 cyclomatic complexity per function (`make complexity`)
- **Dead code**: Vulture must pass (`make dead-code`)
- **Security**: Bandit must pass with no HIGH findings (`make security-lint`)
- **Tests**: all tests must pass (`make test`)
- **Commit messages**: must follow [Conventional Commits](https://www.conventionalcommits.org/) (`make commitlint`)
  - Format: `type(scope): description` — e.g., `fix(api): handle timeout errors`
  - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`

### Test-Driven Development (TDD)

All new features and bug fixes **must** use TDD with vertical slices:

1. **ONE test → ONE implementation → repeat** (never write all tests first)
2. **RED**: Write one failing test for the next behavior
3. **GREEN**: Write minimal code to make it pass
4. **REFACTOR**: Clean up only after GREEN, never while RED
5. Run `make test` after each cycle to confirm

Tests must verify **behavior through public interfaces**, not implementation
details. A test should survive an internal refactor without breaking. See
`.agents/skills/tdd/SKILL.md` for the full TDD skill reference.

### Before Every Commit

Run `make ci` — this runs all CI-equivalent checks locally. If it passes
locally, CI will pass too. Use conventional commit message format (see above).

### Splitting Large Files

- Do not add new files over 500 lines — split proactively
- **Extract Method** to reduce complexity — no behavioral changes
- **Mixins** for splitting large classes (VideoAssembler, SmartPipeline, etc.)
- **Re-export shims** for backwards compatibility (e.g., `assembly.py`)
- When splitting, search for all imports of the moved symbols and update them
- Keep public API in the original file via re-exports when needed

### Architecture Conventions

- **Mixins** inherit from a base mixin and are composed into the main class
- **Helper modules** contain standalone functions extracted from large files
- **Re-export shims** preserve backward compatibility when moving code
- After any structural changes, update `ARCHITECTURE.md` to reflect new layout

### Documentation Freshness

- When modifying user-facing features (CLI flags, UI steps, config options, API behavior),
  **you MUST update the corresponding Docusaurus page** in `docs-site/docs/`.
- Run `make docs-build` after any docs change to verify the build passes.
- The mapping of code → docs pages:
  - CLI commands/flags → `docs-site/docs/cli/`
  - UI wizard changes → `docs-site/docs/ui-walkthrough/`
  - Config options → `docs-site/docs/configuration/`
  - Hardware support → `docs-site/docs/hardware/`
  - Music/audio → `docs-site/docs/music/`
  - New features → `docs-site/docs/features/` (create new page if needed)
- After structural changes, also update `docs-site/sidebars.ts` if new pages were added.

### Makefile Is The Single Source of Truth

- CI (`ci.yml`) uses `make` targets — not raw commands
- Pre-commit hooks use `make` targets where possible
- `CLAUDE.md` references `make` targets — not raw commands
- When adding a new check, add it to the Makefile first, then reference it

## Key Entry Points

- CLI: `src/immich_memories/cli/__init__.py` → `main()`
- Pipeline: `src/immich_memories/analysis/smart_pipeline.py` → `SmartPipeline.run()`
- Assembly: `src/immich_memories/processing/video_assembler.py` → `VideoAssembler.assemble()`
- UI: `src/immich_memories/ui/app.py` → NiceGUI routes
