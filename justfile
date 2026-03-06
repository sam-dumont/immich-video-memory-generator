# Immich Memories - Development Commands
# Run `just --list` to see all available commands

# Default Python version
python_version := "3.13"

# Default recipe - show help
default:
    @just --list

# Install uv if not present
[private]
ensure-uv:
    @command -v uv >/dev/null 2>&1 || (echo "Installing uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)

# Setup development environment
setup: ensure-uv
    uv python install {{python_version}}
    uv sync --all-extras

# Install with Mac-specific dependencies
setup-mac: ensure-uv
    uv python install {{python_version}}
    uv sync --extra mac

# Run the CLI
run *ARGS:
    uv run immich-memories {{ARGS}}

# Launch the UI
ui:
    uv run immich-memories ui

# Check hardware acceleration status
hardware:
    uv run immich-memories hardware

# Run tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run tests with coverage
test-cov:
    uv run pytest --cov=src/immich_memories --cov-report=term-missing

# Type checking
typecheck:
    uv run mypy src/

# Lint code
lint:
    uv run ruff check src/ tests/

# Format code
fmt:
    uv run ruff format src/ tests/
    uv run ruff check --fix src/ tests/

# Run all checks (lint, typecheck, test)
check: lint typecheck test

# Build package
build:
    uv build

# Clean build artifacts
clean:
    rm -rf dist/ build/ *.egg-info/
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

# Update dependencies
update:
    uv lock --upgrade
    uv sync --all-extras

# Show outdated dependencies
outdated:
    uv pip list --outdated

# Generate requirements.txt for pip users
requirements:
    uv pip compile pyproject.toml -o requirements.txt
    uv pip compile pyproject.toml --extra mac -o requirements-mac.txt
    uv pip compile pyproject.toml --extra face -o requirements-face.txt
    uv pip compile pyproject.toml --all-extras -o requirements-all.txt
