# Makefile for immich-memories
# Uses uv for fast Python package management

.PHONY: help install dev run preflight test test-cov lint format typecheck check clean clean-cache clean-all build docker docker-run

# Default target
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Development:"
	@echo "  install      Install production dependencies"
	@echo "  dev          Install all dependencies (including dev)"
	@echo "  run          Run the NiceGUI app"
	@echo "  cli          Run the CLI tool"
	@echo "  preflight    Check all provider connections (Immich, Ollama, etc.)"
	@echo ""
	@echo "Testing:"
	@echo "  test         Run all tests"
	@echo "  test-cov     Run tests with coverage report"
	@echo "  test-fast    Run tests without slow integration tests"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint         Run ruff linter"
	@echo "  format       Format code with ruff"
	@echo "  typecheck    Run mypy type checker"
	@echo "  check        Run all checks (lint + typecheck + test)"
	@echo ""
	@echo "Building:"
	@echo "  build        Build the package"
	@echo "  docker       Build Docker image"
	@echo "  docker-run   Run Docker container"
	@echo ""
	@echo "Cache Management:"
	@echo "  cache-stats           Show analysis cache stats"
	@echo "  video-cache-stats     Show video download cache stats"
	@echo "  thumbnail-cache-stats Show thumbnail cache stats"
	@echo "  all-cache-stats       Show all cache stats"
	@echo "  clean-cache           Clear analysis cache (SQLite)"
	@echo "  clean-video-cache     Clear video file cache"
	@echo "  clean-thumbnail-cache Clear thumbnail cache"
	@echo "  clean-all-cache       Clear all caches"
	@echo ""
	@echo "Cleanup:"
	@echo "  clean        Remove build artifacts"
	@echo "  clean-all    Remove everything (build + cache + venv)"

# =============================================================================
# Development
# =============================================================================

install:
	uv sync --no-dev

dev:
	uv sync --all-extras

# Install with macOS Metal GPU acceleration for TensorFlow
dev-mac:
	uv sync --extra all-mac --extra dev

run:
	uv run python src/immich_memories/ui/app.py

run-debug:
	NICEGUI_LOGGING_LEVEL=DEBUG uv run python src/immich_memories/ui/app.py

cli:
	uv run immich-memories --help

preflight:
	uv run immich-memories preflight -v

# =============================================================================
# Testing
# =============================================================================

test:
	uv run pytest -v

test-cov:
	uv run pytest --cov=src/immich_memories --cov-report=html --cov-report=term-missing
	@echo "Coverage report: htmlcov/index.html"

test-fast:
	uv run pytest -v -m "not slow"

test-watch:
	uv run pytest-watch -- -v

# Run specific test file
test-cache:
	uv run pytest tests/test_cache.py -v

test-scoring:
	uv run pytest tests/test_scoring.py -v

# =============================================================================
# Code Quality
# =============================================================================

lint:
	uv run ruff check src tests

lint-fix:
	uv run ruff check --fix src tests

format:
	uv run ruff format src tests

format-check:
	uv run ruff format --check src tests

typecheck:
	uv run mypy src/immich_memories

# Run all checks
check: lint typecheck test
	@echo "All checks passed!"

# Pre-commit hooks
pre-commit:
	uv run pre-commit run --all-files

# =============================================================================
# Building
# =============================================================================

build:
	uv build

build-wheel:
	uv build --wheel

# =============================================================================
# Docker
# =============================================================================

DOCKER_IMAGE := immich-memories
DOCKER_TAG := latest

docker:
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) -f docker/Dockerfile .

docker-run:
	docker run -it --rm \
		-p 8080:8080 \
		-v ~/.immich-memories:/root/.immich-memories \
		$(DOCKER_IMAGE):$(DOCKER_TAG)

docker-shell:
	docker run -it --rm \
		-v ~/.immich-memories:/root/.immich-memories \
		$(DOCKER_IMAGE):$(DOCKER_TAG) /bin/bash

# =============================================================================
# Cleanup
# =============================================================================

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-cache:
	rm -f ~/.immich-memories/cache.db
	@echo "Analysis cache cleared"

clean-video-cache:
	rm -rf ~/.immich-memories/cache/video-cache
	@echo "Video cache cleared"

clean-thumbnail-cache:
	rm -rf ~/.immich-memories/cache/thumbnails
	@echo "Thumbnail cache cleared"

clean-all-cache: clean-cache clean-video-cache clean-thumbnail-cache
	@echo "All caches cleared"

clean-all: clean clean-all-cache
	rm -rf .venv/
	rm -rf uv.lock
	@echo "All artifacts removed"

# =============================================================================
# Utilities
# =============================================================================

# Show project info
info:
	@echo "Project: immich-memories"
	@echo "Python: $(shell uv run python --version)"
	@echo "Location: $(shell pwd)"
	@echo ""
	@echo "Cache location: ~/.immich-memories/cache.db"
	@echo "Config location: ~/.immich-memories/config.yaml"

# Open cache database with sqlite3
db:
	sqlite3 ~/.immich-memories/cache.db

# Show analysis cache stats
cache-stats:
	@uv run python -c "from immich_memories.cache import VideoAnalysisCache; c = VideoAnalysisCache(); import json; print(json.dumps(c.get_stats(), indent=2))"

# Show video cache stats
video-cache-stats:
	@uv run python -c "from immich_memories.cache import VideoDownloadCache; c = VideoDownloadCache(); import json; print(json.dumps(c.get_stats(), indent=2, default=str))"

# Show thumbnail cache stats
thumbnail-cache-stats:
	@uv run python -c "from immich_memories.cache import ThumbnailCache; c = ThumbnailCache(); import json; print(json.dumps(c.get_stats(), indent=2, default=str))"

# Show all cache stats
all-cache-stats: cache-stats video-cache-stats thumbnail-cache-stats

# Generate version info
version:
	@uv run python -c "from immich_memories._version import __version__; print(__version__)"

# Create a new release (requires GH_TOKEN)
release:
	uv run semantic-release version
	uv run semantic-release publish
