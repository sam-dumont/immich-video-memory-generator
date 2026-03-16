# Contributing to Immich Memories

## About this project

This codebase is built almost entirely with AI (Claude by Anthropic). That's not a disclaimer: it's a deliberate choice, and the quality gates exist because of it, not in spite of it. 1,100+ tests, 17 CI gates, mutation testing, composition over inheritance, TDD.

If you spot something the AI got wrong, please fix it. That's how this gets better.

## AI Tools Welcome (With Context)

This entire project was built with GenAI (Claude by Anthropic). AI-assisted contributions are absolutely welcome: this is not a project that's going to lecture you about using Copilot.

That said, AI code has specific failure modes: bloat, over-abstraction, tests that test mocks instead of behavior, verbose docstrings that add no value. That's why this project has 17 CI gates, mutation testing, and architectural rules like "no mixins": they exist specifically to catch the things AI gets wrong.

When contributing with AI tools:

1. **Mention it in the PR.** A quick "used Claude/Copilot for X" is enough. Not a judgment: just context for review. Following the [Apache Software Foundation approach](https://github.com/melissawm/open-source-ai-contribution-policies).

2. **Review what the AI wrote.** You're responsible for every line. If the AI added a 200-line abstraction for something that needs 20 lines, catch that before submitting.

3. **Run `make ci` and `make critique`.** These gates exist to catch AI-specific smells. If they pass, you're probably fine.

## Code of Conduct

Be respectful and inclusive. This is a hobby project, not a corporation.

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
make test-integration  # Real FFmpeg tests (requires FFmpeg)
make ci                # Full CI pipeline locally
make critique          # AI smell audit
make mutation          # Mutation testing (slow, weekly CI)
```

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
├── processing/   # Video assembly (VideoAssembler + 8 composed services)
├── titles/       # Title screens, maps, globe animation (TitleScreenGenerator + services)
├── audio/        # Music generation, audio ducking, mood analysis
├── ui/           # NiceGUI 4-step wizard
├── cache/        # Analysis, video, and thumbnail caching (SQLite)
├── tracking/     # Run history and job management
├── scheduling/   # Cron-based automatic generation
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

## Getting Help

- **Questions**: [GitHub Discussions](https://github.com/sam-dumont/immich-video-memory-generator/discussions)
- **Bugs**: [GitHub Issues](https://github.com/sam-dumont/immich-video-memory-generator/issues)
- **Security**: See [SECURITY.md](SECURITY.md)

## License

Contributions are licensed under the MIT License.
