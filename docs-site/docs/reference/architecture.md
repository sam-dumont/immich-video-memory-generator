---
title: Architecture
sidebar_label: Architecture
---

# Codebase architecture

How the code is organized and where to make changes.

## Composition over inheritance

The four main orchestrators compose smaller service objects through constructor injection. The
lifecycle a run reports is the `OperationalPhase` enum in `operations/phases.py` (discovery →
download → analysis → selection → render → music → delivery → complete), and it spans two entry
points. Selection runs first: `generate` (or the Memory page's Cut) →
`build_smart_pipeline(editorial_context)` in `analysis/editorial_runtime.py` →
`SmartPipeline.run_editorial_source()` → `RuntimeEditorialPlanner.plan_source()`, which runs
preparation, the two readings, the structure and story planners, and certifies the timing.
`generate_memory()` in `generate.py` then does extract → assemble → music → upload. Hand it no
clips and it raises rather than going to find some.

| Orchestrator | Services | What it does |
|---|---|---|
| **VideoAssembler** | FFmpegProber, ClipEncoder, AssemblyEngine, AudioMixerService, TitleInserter | Assembles clips into final video |
| **SmartPipeline** | RuntimeEditorialPlanner (from `build_smart_pipeline`) | Runs the story-first selection and projects its plan into a `PipelineResult` |
| **ImmichClient** | SearchService, AllAssetsService, AssetService, PersonService, AlbumService | Talks to the Immich API |
| **TitleScreenGenerator** | RenderingService, EndingService, TripService | Creates title/ending screens |

The editorial route has Protocol-typed ports rather than services: the providers and the people
loader, the structure planner, the judges it calls out to, and `EditorialAttempt` in `operations/`
for the durable attempt tree and its OS lease. On disk each attempt is
`<cache>/editorial-runs/<key>/attempts/<id>/`, and the annotation store is
`<cache>/annotations.sqlite`.
[ARCHITECTURE.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/ARCHITECTURE.md)
names every port and the file it lives in, with the full module map.

## CI pipeline

CI runs in tiers, cheap to expensive.

- **Tier 0: cache setup.** Every job that installs the project waits on it.
- **Tier 1: quality gates** (the table below), one job, steps in order, each carrying
  `if: !cancelled()` so the first failure doesn't hide the rest. Commitlint runs on pull requests
  only. A second job runs the security scans in parallel.
- **Tier 2: tests**, after both tier 1 jobs pass. The unit suite (Ubuntu on 3.11/3.12/3.13; macOS on
  3.13 for a pull request, all three on `main`), plus `make test-extras`. Neither extras job pulls
  torch, so what runs there is the subset that survives without it; the rest is a local target.
- **Tier 3: build**, after tests. Package build, and the Docker image on pull requests.

The docs build depends on nothing and starts immediately. The hermetic launch check runs on pull
requests off the cache setup alone: `make launch-check-ci`, Playwright e2e against a fake Immich.

`make ci` runs the same gates locally plus the unit tests; the Makefile is the list. CI adds what
needs a remote or a diff: commitlint, pip-audit, gitleaks, hadolint.

## Quality gates

| Gate | Tool | What it catches |
|---|---|---|
| Lint + format | Ruff | Style, import ordering, unused imports |
| Type check | mypy | Type mismatches, missing annotations |
| Complexity | Xenon + complexipy | Xenon grade C max, cognitive complexity ≤15 |
| File length | Makefile script | Over 800 lines warns, over 1000 fails |
| Dead code | Vulture | Unused functions, variables, imports |
| Duplication | jscpd | Copy-pasted blocks (≤5%) |
| Modernization | refurb | Idioms a newer Python replaced |
| AI smells | `make critique` | Over-structured code, docstrings that restate the signature |
| Security | Bandit + Semgrep | Common vulnerability patterns |
| Secrets | Gitleaks | Committed API keys |
| Dependencies | pip-audit + deptry | Known CVEs; unused, missing or transitive imports |
| Architecture | import-linter | The core packages (`analysis`, `processing`, `titles`, `people`, `store`, `triage`, `operations`) must not import `ui`; they plus `audio` must not import `cli`. UI and CLI import core, never the reverse |
| Compose | `make compose-check` | A `docker-compose.yml` that only parses with the repo beside it |
| Commits | commitizen | Non-conventional commit messages |
| Docs | docs-voice, docs-cli-check, docs-config-check, notices-check | Chatbot prose and em dashes; drift between the generated references and the code |
| Tests | pytest | The unit suite in CI; integration and e2e locally and on the GPU runner |

`docs-voice` and `notices-check` run in `make ci` and the pre-commit hooks, not in the CI job.

## How to add a feature

### A new processing capability

1. Create a service class in the relevant package (for example `processing/my_service.py`)
2. Inject it into the orchestrator's `__init__` in `video_assembler.py`
3. Add tests in `tests/test_my_service.py`

### A new API endpoint

1. Add the method to the relevant service in `api/` (for example `search_service.py`)
2. Add a delegating method on `ImmichClient` in `api/immich.py`
3. Add the model to `api/models.py` if needed, and test against a mock HTTP client

### A new memory type

1. Add the value to the `MemoryType` enum in `memory_types/registry.py`
2. Write a factory function in `memory_types/factory.py` and decorate it with `@register_preset`: the decorator *is* the registration, there is no second list to edit there
3. Add date builder logic if the type needs its own, in `memory_types/date_builders.py`
4. Add it to `OFFERED_MEMORY_TYPES` in `memory_types/registry.py`: `--memory-type` and the Memory page's select both read that tuple, in that order
5. Document it in [`docs-site/docs/create/memory-types.mdx`](../create/memory-types.mdx)

### A new CLI command

1. Create a new file in `cli/` (for example `cli/my_cmd.py`)
2. Register the command group in `cli/__init__.py`
3. Add the docs page under `docs-site/docs/`, add its ID to `docs-site/sidebars.ts`, and run `make docs-build`

## File naming conventions

- `_prefixed.py`: private helpers for their own package. Nothing enforces that, and one cross-package import has leaked in (`generate_privacy.py` reaching into `titles._trip_titles`)
- `*_service.py`: composed service classes
- `*_models.py`: data models (Pydantic or dataclass)
- `*_helpers.py`: standalone helper functions
- `*.py` (no prefix): public modules and standalone classes. Re-export shims belong in
  `__init__.py` and nowhere else
