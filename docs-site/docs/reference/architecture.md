---
title: Architecture
sidebar_label: Architecture
---

# Codebase Architecture

How the code is organized, why it's built this way, and where to make changes.

## Composition over Inheritance

The codebase used to split large classes into mixins. That worked for a while, but mixins create implicit coupling: you can't understand a mixin without knowing what `self` looks like on the host class. When `VideoAssembler` hit 11 mixins, it was time to refactor.

Now the four main orchestrators compose smaller service objects via constructor injection. Above them, `generate_memory()` in `generate.py` is the top-level entry that runs the whole lifecycle (discovery → download → analysis → selection → render → music → delivery):

| Orchestrator | Services | What it does |
|---|---|---|
| **VideoAssembler** | FFmpegProber, FilterBuilder, ClipEncoder, AssemblyEngine, AudioMixerService, TitleInserter | Assembles clips into final video |
| **SmartPipeline** | ClipAnalyzer, PreviewBuilder, ClipRefiner, ClipScaler, SelectionQuality | Analyzes and selects the best clips |
| **ImmichClient** | SearchService, AllAssetsService, AssetService, PersonService, AlbumService | Talks to the Immich API |
| **TitleScreenGenerator** | RenderingService, EndingService, TripService | Creates title/ending screens |

Each service is a standalone class you can test in isolation. The orchestrator wires them together in `__init__` and delegates work.

## CI Pipeline Structure

CI runs in tiers, cheap to expensive. If lint fails in 10 seconds, there's no point waiting 3 minutes for tests to tell you the same thing.

**Tier 0: Cache setup** (every job that installs the project waits on it; the docs build doesn't)

**Tier 1: Cheap quality gates** — one job, run as steps in order. Each carries `if: !cancelled()`,
so the first failure doesn't hide the ones behind it and you get the whole list from one run:
- Commit message linting (Conventional Commits)
- Ruff lint + format check
- mypy type checking
- Dead code detection (Vulture)
- Cyclomatic complexity (Xenon grade C)
- Cognitive complexity (complexipy)
- File length (800-line soft limit warns, 1000-line hard limit fails)
- Refurb modernization checks
- Dependency hygiene (deptry)
- Architecture layer enforcement (import-linter)
- Code duplication detection (jscpd)
- CLI and config reference drift (the generated pages must match the Click tree and the pydantic schema)
- AI code critique

**Tier 2: Security** (parallel with Tier 1):
- Bandit static analysis
- Semgrep rules
- pip-audit dependency CVEs
- Gitleaks secret detection
- Hadolint Dockerfile linting

**Tier 3: Tests** (runs after both Tier 1 and Tier 2 pass):
- Full test suite (Ubuntu on 3.11/3.12/3.13; macOS on 3.13 for a pull request, all three on main)
- `make test-extras`: only the tests marked `extras`, which are what the torch family
  (audio-ml/demucs) unlocks

**Tier 4: Build + Docker** (runs after tests pass):
- Package build verification
- Docker image build

Two jobs sit outside the tiers. The docs site build depends on nothing and starts immediately. The
hermetic launch check runs on pull requests off the cache setup alone, in parallel with the tests:
CI calls `make launch-check-ci`, which is the Playwright e2e run against a fake Immich. The local
`make launch-check` is the bigger one that also does `check`, `build`, and the docs build.

A PR passes 20 gates: 15 static checks in the quality job and 5 security scans in the security job. Locally, `make ci` runs 16 of them plus the unit tests: lint, format-check, typecheck, file-length, complexity, cognitive-complexity, dead-code, security-lint, semgrep, refurb, dep-check, arch-check, duplication, critique, docs-cli-check, docs-config-check, test. CI adds the four that need a remote or a diff (commitlint, pip-audit, gitleaks, hadolint), then the build/docker/docs jobs and the launch check. Every PR must pass all of them.

## Quality Gates Overview

| Gate | Tool | What it catches |
|---|---|---|
| Lint + format | Ruff | Style issues, import ordering, unused imports |
| Type check | mypy | Type mismatches, missing annotations |
| Complexity | Xenon | Functions too complex to reason about (grade C max) |
| File length | Makefile script | Files over 800 lines warn, over 1000 fail (split into services) |
| Dead code | Vulture | Unused functions, variables, imports |
| Duplication | jscpd | Copy-pasted code blocks (≤5%) |
| Security | Bandit + Semgrep | Common vulnerability patterns |
| Secrets | Gitleaks | Accidentally committed API keys |
| Dependencies | pip-audit + deptry | Known CVEs; unused, missing or transitive imports |
| Architecture | import-linter | Two forbidden-import contracts: `analysis`/`processing`/`titles` must not import `ui`, and those three plus `audio` must not import `cli`. The dependency runs one way — UI and CLI import core, never the reverse |
| Commits | commitizen | Non-conventional commit messages |
| Tests | pytest | 5,600+ tests: 5,000+ unit in CI, 600+ integration/E2E locally and on the GPU runner |

## How to Add a New Feature

### Adding a new processing capability

1. Create a service class in the relevant package (e.g., `processing/my_service.py`)
2. Keep it under 800 lines (soft limit; 1000 is the hard CI failure). If it needs more, split into a service + helpers file
3. Inject it into the orchestrator's `__init__` in `video_assembler.py`
4. Add tests in `tests/test_my_service.py`
5. Run `make ci` before committing — `make check` is the fast subset and skips the drift, security and duplication gates

### Adding a new API endpoint

1. Add the method to the relevant service in `api/` (e.g., `search_service.py`)
2. Add a delegating method on `ImmichClient` in `api/immich.py`
3. Add the model to `api/models.py` if needed
4. Test against a mock HTTP client

### Adding a new memory type

1. Add the value to the `MemoryType` enum in `memory_types/registry.py`
2. Write a factory function in `memory_types/factory.py` and decorate it with `@register_preset` — the decorator *is* the registration, there is no second list to edit there
3. Add date builder logic if the type needs its own, in `memory_types/date_builders.py`
4. Add the string to the `--memory-type` choice list in `cli/generate_options.py`. That list is hand-written, not derived from the enum, so a type you skip here exists everywhere except the CLI
5. Add a page under `docs-site/docs/create/memory-types/`

### Adding a new CLI command

1. Create a new file in `cli/` (e.g., `cli/my_cmd.py`)
2. Register the command group in `cli/__init__.py`
3. Add corresponding docs in `docs-site/docs/`

### Adding a docs page

1. Create the markdown file in the appropriate `docs-site/docs/` subdirectory
2. Add the page ID to `docs-site/sidebars.ts`
3. Run `make docs-build` to verify it compiles

## File Naming Conventions

- `_prefixed.py`: private helpers, meant for their own package. Nothing enforces that — import-linter only guards the core/UI and core/CLI directions — and a couple of cross-package imports have leaked in
- `*_service.py`: composed service classes
- `*_models.py`: data models (Pydantic or dataclass)
- `*_helpers.py`: standalone helper functions
- `*.py` (no prefix): public modules and standalone classes. Re-export shims belong in
  `__init__.py` and nowhere else
