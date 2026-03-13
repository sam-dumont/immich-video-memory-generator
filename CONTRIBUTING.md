# Contributing to Immich Memories

Thank you for your interest in contributing to Immich Memories! This document provides guidelines and instructions for contributing.

## Context: AI-built codebase

> This project is written almost entirely with AI (Claude by Anthropic) as an experiment in
> pushing AI-assisted development on a real, complex codebase. The quality gates are strict
> (970+ tests, type checking, complexity limits, security scanning), so don't be shy about
> holding contributions to the same bar. If you spot something the AI got wrong, fix it.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment for everyone.

## How to Contribute

### Reporting Bugs

1. **Check existing issues** - Search [GitHub Issues](https://github.com/sam-dumont/immich-video-memory-generator/issues) to see if the bug has already been reported
2. **Create a new issue** - If not found, create a new issue with:
   - Clear, descriptive title
   - Steps to reproduce the bug
   - Expected vs actual behavior
   - Your environment (OS, Python version, hardware)
   - Relevant logs or error messages

### Suggesting Features

1. **Check existing issues** - Look for similar feature requests
2. **Create a feature request** - Describe:
   - The problem you're trying to solve
   - Your proposed solution
   - Alternative solutions you've considered
   - Any additional context

### Pull Requests

**Keep PRs small: under 200-300 lines of diff.** Large PRs are hard to review and tend to hide bugs. If your change is bigger, split it into focused, reviewable chunks (one concern per PR). Yes, the pre-v1 history has some monster PRs. Do as I say, not as I did.

1. **Fork the repository**
2. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Set up development environment**:
   ```bash
   # Install uv (if not already installed)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install all dependencies (including dev)
   make dev
   ```
4. **Make your changes**
5. **Run all checks**:
   ```bash
   make check  # Runs lint, format-check, typecheck, file-length, complexity, and tests
   ```
6. **Commit your changes**:
   ```bash
   git commit -m "feat: add amazing feature"
   ```
7. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Open a Pull Request** against the `main` branch

## Development Setup

### Prerequisites

- Python 3.11+ (3.13 recommended)
- FFmpeg
- uv (recommended) or pip
- GNU Make (pre-installed on most systems)

### Quick Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/immich-video-memory-generator.git
cd immich-video-memory-generator

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
make dev

# Verify everything works
make check
```

### Development Commands

The **Makefile** is the single source of truth for all commands. Run `make help` to see everything available.

```bash
make dev           # Install all dependencies (including dev)
make test          # Run tests
make test-cov      # Run tests with coverage
make lint          # Run ruff linter
make format        # Format code with ruff
make typecheck     # Run mypy type checker
make file-length   # Check all .py files are ≤500 lines
make complexity    # Check cyclomatic complexity (Xenon grade C)
make check         # Run all checks (lint + format + type + length + complexity + test)
make ci            # Full CI-equivalent pipeline (all checks + dead-code)
make cli           # Run the CLI
make run           # Launch the UI
make build         # Build package
make clean         # Remove build artifacts
```

## Code Style

### Python

- The project uses **Ruff** for linting and formatting
- Code is automatically formatted on CI
- Type hints are required for all public functions
- Follow PEP 8 naming conventions

### Commit Messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

Examples:
```
feat(ui): add keyboard shortcuts for moment refinement
fix(api): handle pagination correctly for large libraries
docs: update installation instructions for macOS
```

### Documentation

- Docs live in `docs-site/docs/` and are built with [Docusaurus](https://docusaurus.io/)
- Preview locally: `make docs-dev` (opens at http://localhost:3000)
- Build: `make docs-build`
- **When changing user-facing behavior**, update the corresponding docs page:
  - CLI commands/flags → `docs-site/docs/cli/`
  - UI wizard changes → `docs-site/docs/ui-walkthrough/`
  - Config options → `docs-site/docs/configuration/`
  - Hardware support → `docs-site/docs/hardware/`
  - Music/audio → `docs-site/docs/music/`
  - New features → `docs-site/docs/features/` (create new page if needed)
- If adding new pages, update `docs-site/sidebars.ts`
- To add screenshots: run the Playwright script (`npx tsx scripts/take-screenshots.ts`),
  blur faces (`npx tsx scripts/blur-faces.ts`), and commit to `static/img/screenshots/`
- Add docstrings to all public functions and classes
- Include type hints in function signatures

## Testing

### Running Tests

```bash
# All tests
make test

# With coverage
make test-cov

# Specific test file
uv run pytest tests/test_specific.py

# Specific test
uv run pytest tests/test_specific.py::test_function_name -v
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Use descriptive test names: `test_should_detect_faces_in_video`
- Use fixtures for common setup
- Mock external services (Immich API, FFmpeg)

## Project Structure

The codebase enforces a 500-line limit per `.py` file. Large classes use mixins to split logic while keeping a single public API. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module listing with 23+ files per package.

```
immich-memories/
├── src/immich_memories/
│   ├── cli/                # CLI commands (Click)
│   ├── config.py           # Configuration (re-exports from config_loader, config_models)
│   ├── api/                # Immich API client (SyncImmichClient + 3 mixins)
│   ├── analysis/           # Video analysis & clip selection (SmartPipeline + 4 mixins)
│   ├── processing/         # Video processing (VideoAssembler + 11 mixins)
│   ├── audio/              # Audio processing, music generation, mood analysis
│   ├── titles/             # Title screen generation (PIL, Taichi, FFmpeg renderers)
│   ├── tracking/           # Run history & telemetry (SQLite)
│   ├── ui/                 # NiceGUI web interface
│   │   ├── app.py
│   │   └── pages/          # Step-by-step wizard pages
│   └── cache/              # Thumbnail, video, and analysis caching
├── tests/                  # Test files
├── docker/                 # Docker configuration
├── deploy/                 # Kubernetes & Terraform deployment
├── .github/workflows/      # CI/CD workflows
├── pyproject.toml          # Project configuration
├── Makefile                # Single source of truth for all commands
└── README.md
```

## Hardware-Specific Development

### macOS (Apple Silicon)

For Vision framework development:

```bash
# Install Mac-specific dependencies (includes Metal GPU acceleration)
make dev-mac

# Test Vision framework
python -c "from immich_memories.analysis.apple_vision import is_vision_available; print(is_vision_available())"
```

### NVIDIA (CUDA)

For CUDA development:

```bash
# Ensure CUDA toolkit is installed
nvcc --version

# Test hardware detection
uv run immich-memories hardware
```

## Release Process

Releases are automated via GitHub Actions:

1. Update version in `pyproject.toml`
2. Create and push a git tag:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
3. Create a GitHub Release from the tag
4. CI will automatically:
   - Run all tests
   - Build the package
   - Publish to PyPI
   - Build and push Docker images

## Getting Help

- **Questions**: Open a [GitHub Discussion](https://github.com/sam-dumont/immich-video-memory-generator/discussions)
- **Bugs**: Open a [GitHub Issue](https://github.com/sam-dumont/immich-video-memory-generator/issues)
- **Security**: See [SECURITY.md](SECURITY.md) for reporting vulnerabilities

## License

By contributing to Immich Memories, you agree that your contributions will be licensed under the MIT License.
