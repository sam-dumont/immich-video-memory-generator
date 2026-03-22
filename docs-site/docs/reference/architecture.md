---
title: Architecture
sidebar_label: Architecture
---

# Codebase Architecture

How the code is organized, why it's built this way, and where to make changes.

## Composition over Inheritance

The codebase used to split large classes into mixins. That worked for a while, but mixins create implicit coupling: you can't understand a mixin without knowing what `self` looks like on the host class. When `VideoAssembler` hit 11 mixins, it was time to refactor.

Now the four main orchestrators compose smaller service objects via constructor injection:

| Orchestrator | Services | What it does |
|---|---|---|
| **VideoAssembler** | FFmpegProber, FilterBuilder, ClipEncoder, AssemblyEngine, AudioMixerService, TitleInserter | Assembles clips into final video |
| **SmartPipeline** | ClipAnalyzer, PreviewBuilder, ClipRefiner, ClipScaler | Analyzes and selects the best clips |
| **ImmichClient** | SearchService, AllAssetsService, AssetService, PersonService, AlbumService | Talks to the Immich API |
| **TitleScreenGenerator** | RenderingService, EndingService, TripService | Creates title/ending screens |

Each service is a standalone class you can test in isolation. The orchestrator wires them together in `__init__` and delegates work.

## CI Pipeline Structure

CI runs in tiers, cheap to expensive. If lint fails in 10 seconds, there's no point waiting 3 minutes for tests to tell you the same thing.

**Tier 0: Cache setup** (shared by all jobs)

**Tier 1: Cheap quality gates** (~10s each, all parallel):
- Commit message linting (Conventional Commits)
- Ruff lint + format check
- mypy type checking
- Dead code detection (Vulture)
- Cyclomatic complexity (Xenon grade C)
- Cognitive complexity checks
- File length enforcement (800 lines max)
- Refurb modernization checks
- Dependency vulnerability audit (pip-audit)
- Docstring coverage
- Architecture layer enforcement
- Code duplication detection
- AI code critique

**Tier 2: Security** (parallel with Tier 1):
- Bandit static analysis
- Semgrep rules
- Gitleaks secret detection
- Hadolint Dockerfile linting

**Tier 3: Tests** (runs after Tier 1 passes):
- Full test suite
- Tests with optional extras (Taichi GPU, etc.)

**Tier 4: Build + Docker** (runs after tests pass):
- Package build verification
- Docker image build
- Docs site build

17 quality gates total. Every PR must pass all of them.

## Quality Gates Overview

| Gate | Tool | What it catches |
|---|---|---|
| Lint + format | Ruff | Style issues, import ordering, unused imports |
| Type check | mypy | Type mismatches, missing annotations |
| Complexity | Xenon | Functions too complex to reason about (grade C max) |
| File length | Custom script | Files over 800 lines (split into services) |
| Dead code | Vulture | Unused functions, variables, imports |
| Duplication | Custom | Copy-pasted code blocks |
| Security | Bandit + Semgrep | Common vulnerability patterns |
| Secrets | Gitleaks | Accidentally committed API keys |
| Dependencies | pip-audit | Known CVEs in dependencies |
| Docstrings | Custom | Missing documentation on public APIs |
| Architecture | Custom | Layer violations (e.g., UI importing from processing internals) |
| Commits | commitizen | Non-conventional commit messages |
| Tests | pytest | 1,000+ tests covering unit, integration, and benchmarks |

## How to Add a New Feature

### Adding a new processing capability

1. Create a service class in the relevant package (e.g., `processing/my_service.py`)
2. Keep it under 800 lines. If it needs more, split into a service + helpers file
3. Inject it into the orchestrator's `__init__` in `video_assembler.py`
4. Add tests in `tests/test_my_service.py`
5. Run `make check` before committing

### Adding a new API endpoint

1. Add the method to the relevant service in `api/` (e.g., `search_service.py`)
2. Add a delegating method on `ImmichClient` in `api/immich.py`
3. Add the model to `api/models.py` if needed
4. Test against a mock HTTP client

### Adding a new memory type

1. Add the type to `MemoryType` enum in `memory_types/registry.py`
2. Create a factory function in `memory_types/factory.py`
3. Register it in the factory registry
4. Add date builder logic if needed in `memory_types/date_builders.py`

### Adding a new CLI command

1. Create a new file in `cli/` (e.g., `cli/my_cmd.py`)
2. Register the command group in `cli/__init__.py`
3. Add corresponding docs in `docs-site/docs/`

### Adding a docs page

1. Create the markdown file in the appropriate `docs-site/docs/` subdirectory
2. Add the page ID to `docs-site/sidebars.ts`
3. Run `make docs-build` to verify it compiles

## File Naming Conventions

- `_prefixed.py`: private helpers, same package only
- `*_service.py`: composed service classes
- `*_models.py`: data models (Pydantic or dataclass)
- `*_helpers.py`: standalone helper functions
- `*.py` (no prefix): public modules, re-export shims, or standalone classes
