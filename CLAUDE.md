# Claude Code Project Instructions

## Quick Start

1. **Install dev dependencies first**: `make dev`
2. Read `ARCHITECTURE.md` — it maps the full codebase structure, key classes,
   data flow, and composition architecture. This avoids needing to explore the repo.

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

# File length gate (≤800 lines per .py file)
make file-length

# Complexity gate (Xenon grade C)
make complexity

# Dead code detection (Vulture)
make dead-code

# Security lint (Bandit)
make security-lint

# Cognitive complexity gate (complexipy ≤15)
make cognitive-complexity

# Code duplication detection (jscpd, requires npm)
make duplication

# Modernization lint (refurb)
make refurb

# Dependency hygiene (deptry: hallucinated/unused/transitive deps)
make dep-check


# Architectural boundary enforcement (import-linter)
make arch-check

# Diff coverage for PRs (≥80% on changed lines)
make diff-cover

# Commit message lint (conventional commits)
make commitlint

# Dependency vulnerability audit
make pip-audit

# Run ALL checks (lint + format + typecheck + file-length + complexity + test)
make check

# Full CI pipeline (all checks + advanced quality gates)
make ci

# Pre-commit hooks (runs all local hooks: lint, format, mypy, gitleaks, commitizen, file-length, complexity, dead-code, security-lint)
make pre-commit

# Self-critique check for AI code smells
make critique
```

## Rules

### Code Quality Gates (enforced in CI)

- **Lint**: ruff check must pass (`make lint`)
- **Format**: ruff format must pass (`make format-check`)
- **Type check**: mypy must pass (`make typecheck`)
- **Max file length**: 800 lines per `.py` file (`make file-length`)
- **Cyclomatic complexity**: Xenon grade C — ≤20 per function (`make complexity`)
- **Cognitive complexity**: complexipy ≤15 per function (`make cognitive-complexity`)
- **Dead code**: Vulture must pass (`make dead-code`)
- **Security**: Bandit must pass with no HIGH findings (`make security-lint`)
- **Modernization**: refurb must pass (`make refurb`)
- **Dependency hygiene**: deptry must pass (`make dep-check`)
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

- Do not add new files over 800 lines — split proactively
- Split along **cohesion boundaries**, not arbitrary line counts
- Extract a service class with a Protocol contract for its dependencies
- The new module should be independently testable and importable
- Do NOT create mixins — use composition with constructor injection
- Do NOT create re-export shims for a single consumer — update import sites directly
- If you can't find a natural split, the file may genuinely be one cohesive unit.
  In that case, look for helper utilities to extract, not arbitrary method groups.
- When splitting, search for all imports of the moved symbols and update them
- After any structural changes, update `ARCHITECTURE.md` to reflect new layout

### Anti-Patterns (DO NOT)

**Docstrings:**
- Do NOT write docstrings that restate the function signature
- Do NOT docstring private/internal functions unless behavior is non-obvious
- Public API functions MUST have docstrings explaining behavior, not just restating the signature

**Abstractions:**
- Do NOT create re-export shims for a single consumer — import from the source
- Re-export shims belong only in `__init__.py` — never in regular modules
- No `_`-prefixed overflow files — give every file a descriptive name
- Do NOT split files purely for line count — splits must follow cohesion boundaries
- Do NOT use mixins — use composition with Protocol contracts
- If you need state from another module, inject it via constructor

**Tests:**
- Do NOT test dataclass field access, property getters, or Python arithmetic
- Do NOT mock more than 3 boundaries in a single test — if you need more, the code under test has too many dependencies
- Every mock MUST have a `# WHY:` comment explaining what external boundary it replaces
- Integration tests (`make test-integration`) must exist for any FFmpeg pipeline change
- Integration tests run locally via pre-commit hook (not in CI) when processing/titles code changes

**Comments:**
- Do NOT write comments that describe what the next line does
- DO write comments that explain WHY something non-obvious is done
- Do NOT leave "extracted from X to stay within 500-line limit" comments

**Config:**
- New config options MUST have a sane default
- User-facing options go in Tier 1 (top-level YAML). Everything else in Tier 2 (`advanced:`)
- Tier 2 sections: analysis, hardware, llm, musicgen, ace_step, content_analysis, audio_content, server
- At runtime, all sections are flat on Config (`config.analysis`, not `config.advanced.analysis`)
- Do NOT add migration/compat shims for renamed fields — deprecate, document, remove

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

### Self-Critique Protocol

After completing any major feature or refactor (>5 files changed), audit your own work:

1. `make ci` passes (baseline)
2. Skeptic review:
   - Would a senior engineer reject any of these patterns?
   - Are there docstrings that add no information?
   - Are there abstractions that serve no consumer?
   - Are tests testing behavior or testing mocks?
   - Are file splits following cohesion or line count?
3. AI hallmark check:
   - Unnecessary type annotations on obvious code?
   - Over-structured solutions (registry pattern for 3 items)?
   - Verbose module-level docstrings with ASCII art?
   - Helper functions called from exactly one place?
4. `make critique` — automated checks for common smells

### Post-Release Development Workflow

After initial release, the project follows **trunk-based development** with small, frequent PRs:

**Branch workflow:**
- `main` is always deployable — no direct pushes after release
- Feature branches: `feat/`, `fix/`, `docs/`, `refactor/` prefixes
- Short-lived branches — merge within 1-2 days, not weeks
- Each feature/fix gets its own PR with clear scope

**PR discipline:**
- Max ~300 lines per PR (excluding generated files, lock files)
- One concern per PR — don't mix refactoring with features
- PR title follows conventional commits format
- Every PR must have a linked GitHub Issue (create one if it doesn't exist)
- CI must pass before merge — no "fix in next PR" exceptions

**Issue-first development:**
1. Create a GitHub Issue describing what and why
2. Create branch from `main`
3. Implement with TDD (RED → GREEN → REFACTOR)
4. Open PR, link to issue
5. CI passes → review → merge → delete branch

**AI-assisted development rules:**
- AI can implement, but humans review every PR before merge
- No mega-commits — if the AI generates 2000 lines, split into multiple PRs
- Every AI session should produce at most 1-2 PRs, not a 40-commit branch
- Run `make critique` before opening any PR
- The `CLAUDE.md` anti-patterns section applies to all AI-generated code

## Key Entry Points

- CLI: `src/immich_memories/cli/__init__.py` → `main()`
- Pipeline: `src/immich_memories/analysis/smart_pipeline.py` → `SmartPipeline.run()`
- Assembly: `src/immich_memories/processing/video_assembler.py` → `VideoAssembler.assemble()`
- UI: `src/immich_memories/ui/app.py` → NiceGUI routes
