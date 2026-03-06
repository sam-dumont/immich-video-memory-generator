# Contributing to Immich Memories

Thank you for your interest in contributing to Immich Memories! This document provides guidelines and instructions for contributing.

## Important Context: AI-Generated Codebase

> **This project was developed primarily using AI assistance (Claude by Anthropic).**
>
> This means:
> - The codebase may have inconsistencies or suboptimal patterns
> - Some edge cases may not be fully handled
> - Your contributions to improve code quality are especially valuable
> - Don't hesitate to refactor or improve existing AI-generated code
>
> We welcome all contributions that improve reliability, performance, security, or maintainability.

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

1. **Fork the repository**
2. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Set up development environment**:
   ```bash
   # Install uv (if not already installed)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install just (command runner)
   brew install just  # macOS
   # or: cargo install just

   # Setup development environment
   just setup
   ```
4. **Make your changes**
5. **Run all checks**:
   ```bash
   just check  # Runs lint, typecheck, and tests
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

- Python 3.12+ (3.13 recommended)
- FFmpeg
- uv (recommended) or pip
- just (recommended) for task running

### Quick Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/immich-video-memory-generator.git
cd immich-video-memory-generator

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup environment
just setup

# Verify everything works
just check
```

### Development Commands

```bash
just setup         # Install dependencies
just test          # Run tests
just test-cov      # Run tests with coverage
just lint          # Run linter
just fmt           # Format code
just typecheck     # Run type checker
just check         # Run all checks (lint, typecheck, test)
just run --help    # Run the CLI
just ui            # Launch the UI
just build         # Build package
just clean         # Clean build artifacts
```

### Manual Commands (without just)

```bash
# Install dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run linter
uv run ruff check src/ tests/

# Format code
uv run ruff format src/ tests/

# Type check
uv run mypy src/

# Run CLI
uv run immich-memories --help
```

## Code Style

### Python

- We use **Ruff** for linting and formatting
- Code is automatically formatted on CI
- Type hints are required for all public functions
- Follow PEP 8 naming conventions

### Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

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

- Update README.md for user-facing changes
- Add docstrings to all public functions and classes
- Include type hints in function signatures

## Testing

### Running Tests

```bash
# All tests
just test

# With coverage
just test-cov

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

```
immich-memories/
├── src/immich_memories/
│   ├── __init__.py
│   ├── cli.py              # CLI commands
│   ├── config.py           # Configuration handling
│   ├── api/                # Immich API client
│   │   ├── immich.py
│   │   └── models.py
│   ├── analysis/           # Video analysis
│   │   ├── scenes.py       # Scene detection
│   │   ├── scoring.py      # Interest scoring
│   │   ├── duplicates.py   # Duplicate detection
│   │   └── apple_vision.py # macOS Vision framework
│   ├── processing/         # Video processing
│   │   ├── clips.py        # Clip extraction
│   │   ├── transforms.py   # Aspect ratio transforms
│   │   ├── assembly.py     # Video assembly
│   │   └── hardware.py     # Hardware acceleration
│   └── ui/                 # NiceGUI web interface
│       ├── app.py
│       └── pages/          # Step-by-step wizard pages
├── tests/                  # Test files
├── docker/                 # Docker configuration
├── .github/workflows/      # CI/CD workflows
├── pyproject.toml          # Project configuration
├── justfile                # Development commands
└── README.md
```

## Hardware-Specific Development

### macOS (Apple Silicon)

For Vision framework development:

```bash
# Install Mac-specific dependencies
uv sync --extra mac

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
   git tag v0.1.0
   git push origin v0.1.0
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
