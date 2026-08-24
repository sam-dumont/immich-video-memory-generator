# Contributing to Immich Memories

## The 60-second version

1. **Open an Issue first.** Let's agree on the approach before you write code.
2. `make dev` installs everything. `make ci` runs the same gates CI runs.
3. Keep the PR to ~300 lines, one concern, a [conventional commit](https://www.conventionalcommits.org/) title.
4. `make ci` must pass before you request review.

That's the whole must-read. Everything below is detail for when you need it.

## How I Prefer to Work

I'm a solo maintainer. Here's what helps me most:

**Open an Issue first.** Before writing code, open an Issue or Discussion describing what you want to change and why. Let me weigh in on the approach before you invest time. This prevents wasted work on both sides.

**Ideas and bug reports are very welcome.** You don't have to write code to contribute. A well-described bug report or feature idea is worth more than a 500-line PR I didn't ask for.

**PRs are welcome too, but keep them focused:**
- Max ~300 lines of diff (excluding generated files, lock files)
- One concern per PR: don't mix refactoring with features
- Link to the Issue you're addressing
- `make ci` must pass before requesting review

## Development Setup

Prerequisites: Python 3.11+, FFmpeg, [uv](https://docs.astral.sh/uv/), GNU Make

```bash
git clone https://github.com/YOUR_USERNAME/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev       # Install all dependencies
make check     # Verify everything works
```

The **Makefile** is the single source of truth. Run `make help` to see everything.

Key commands:
```bash
make test              # Unit tests
make test-integration  # Real FFmpeg + integration tests (requires FFmpeg)
make ci                # Full CI pipeline locally
make critique          # AI smell audit
```

### Testing tiers

| Tier | Where | Command | What it needs |
|------|-------|---------|---------------|
| **Unit tests** | CI + local | `make test` | Nothing external |
| **Integration tests** | Local + CI (FFmpeg-only subset) + GPU runner | `make test-integration` | FFmpeg + Immich server |

**Unit tests** cover pure logic: scoring math, config parsing, data models, helpers.
They run in CI on every PR. No FFmpeg, no Immich, no network needed.

**Integration tests** cover the real pipeline: download from Immich, FFmpeg assembly,
video output validation. Locally they need your Immich server and FFmpeg, and skip
gracefully if either is missing. CI runs the FFmpeg-only subset that your diff touches
(see [If diff-cover fails on your PR](#if-diff-cover-fails-on-your-pr)), and a
self-hosted Linux GPU runner runs the full set.

**What integration tests cover that unit tests can't:**
- FFmpeg filter graph construction and assembly
- Real video download from Immich API (read-only, no writes)
- End-to-end `generate_memory()` pipeline
- Crossfade/transition rendering
- Title screen generation with Taichi GPU

## Code Rules

These are enforced by CI and pre-commit hooks. Not suggestions.

**Architecture:**
- Composition over inheritance: no mixins, no class hierarchies
- File limit: 800 lines soft warning, 1000 lines hard error
- Split along cohesion boundaries, not line counts
- Use Protocol contracts for service dependencies

**Tests:**
- TDD with vertical slices (RED → GREEN → REFACTOR)
- Test behavior through public APIs, not internal methods
- Every mock gets a `# WHY:` comment explaining what boundary it replaces
- No testing Python arithmetic, Pydantic defaults, or ABC instantiation
- Integration tests exist for FFmpeg pipeline changes

**Style:**
- Ruff for linting and formatting
- mypy for type checking (no new suppressions without a clear reason)
- Conventional commits: `feat(scope): description`
- No docstrings that restate the function signature

Full rules in [CLAUDE.md](CLAUDE.md) (yes, the AI reads it too).

## Project Structure

```
src/immich_memories/
├── api/          # Immich API client (ImmichClient + 5 composed services)
├── analysis/     # Video analysis, scoring, clip selection (SmartPipeline + services)
├── speech/       # VAD + transcription for cut placement
├── photos/       # Photo-to-video animation (Ken Burns, face-aware pan, blurred fill)
├── processing/   # Video assembly (VideoAssembler + 6 composed services)
├── titles/       # Title screens, map fly-overs (TitleScreenGenerator + services)
├── audio/        # Music generation, audio ducking, mood analysis
├── ui/           # NiceGUI 4-step wizard
├── cli/          # Click commands
├── cache/        # Analysis, video, and thumbnail caching (SQLite)
├── tracking/     # Run history and job management
├── operations/   # Lifecycle phases, storage report
├── planning/     # Auto-duration planning
├── scheduling/   # Cron-based automatic generation
├── automation/   # auto suggest/run
└── memory_types/ # Preset system (Year in Review, Trip, Person, etc.)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module map.

## Commit Messages

[Conventional Commits](https://www.conventionalcommits.org/) format, enforced by commitlint:

```
feat(ui): add keyboard shortcuts for clip review
fix(api): handle pagination for large libraries
docs: update installation for macOS
refactor(analysis): extract scoring helpers
test: add integration test for HDR passthrough
```

## About this project

This codebase is built almost entirely with AI (Claude by Anthropic). That's not a disclaimer: it's a deliberate choice, and the quality gates exist because of it, not in spite of it. 5,600+ tests, 20 CI gates, 80% diff-coverage on every PR, composition over inheritance, TDD.

If you spot something the AI got wrong, please fix it. That's how this gets better.

## AI Tools Welcome (With Context)

AI-assisted contributions are absolutely welcome: this is not a project that's going to lecture you about using Copilot.

That said, AI code has specific failure modes: bloat, over-abstraction, tests that test mocks instead of behavior, verbose docstrings that add no value. That's why this project has 20 CI gates, a hermetic browser launch test, and architectural rules like "no mixins": they exist specifically to catch the things AI gets wrong.

When contributing with AI tools:

1. **Mention it in the PR.** A quick "used Claude/Copilot for X" is enough. Not a judgment: just context for review. Following the approach in this [collection of open-source AI contribution policies](https://github.com/melissawm/open-source-ai-contribution-policies).

2. **Review what the AI wrote.** You're responsible for every line. If the AI added a 200-line abstraction for something that needs 20 lines, catch that before submitting.

3. **Run `make ci` and `make critique`.** These gates exist to catch AI-specific smells. If they pass, you're probably fine.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold this standard.

## If diff-cover fails on your PR

Skip this until CI tells you otherwise.

Every PR must have 80% coverage on the lines it changes. Code that can only run
with real FFmpeg used to be impossible to cover: `tests/*-coverage.xml` is
gitignored, so integration coverage produced on your machine could never reach
CI, and the gate would fail no matter what you did.

**CI now handles that for you.** Before checking diff-cover it runs the
FFmpeg-only integration suites covering the paths you changed, and only those,
so a PR touching nothing FFmpeg-reachable runs none of them. That job already
has FFmpeg installed, so this costs no setup time.

To reproduce locally exactly what CI will see:

```bash
make integration-coverage-for-diff   # runs only the suites your diff touches
make diff-cover-local                # merges them with unit coverage, same as CI
```

If diff-cover still fails, the uncovered lines are not reachable from an
integration suite and need unit tests. Subprocess boundaries can be stubbed
rather than run for real: `tests/test_ffmpeg_pipe.py` shows the pattern, patch
`subprocess.Popen`, hand back a fake process, and assert on what the code does
with it.

Do not try to commit coverage XMLs. They are gitignored deliberately, and
`git add -f` is not acceptable in this repo.

## Getting Help

- **Questions**: [GitHub Discussions](https://github.com/sam-dumont/immich-video-memory-generator/discussions)
- **Bugs**: [GitHub Issues](https://github.com/sam-dumont/immich-video-memory-generator/issues)
- **Security**: See [SECURITY.md](SECURITY.md)

This is a hobby project maintained in spare time: issues and PRs are answered on a best-effort
basis, usually within a few days, sometimes longer. No SLA. If something is urgent for you, say
so in the issue and include logs, it helps.

## License

Contributions are licensed under the MIT License.
