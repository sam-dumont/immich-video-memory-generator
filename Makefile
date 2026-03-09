# Makefile for immich-memories
# Uses uv for fast Python package management

.PHONY: help install dev run preflight test test-cov lint format typecheck check clean clean-cache clean-all build docker docker-run file-length complexity security-lint dead-code ci ensure-dev

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
	@echo "  file-length  Check all .py files are ≤500 lines"
	@echo "  complexity   Check cyclomatic complexity (Xenon grade C)"
	@echo "  dead-code    Detect dead code (Vulture)"
	@echo "  security-lint Run Bandit security linter"
	@echo "  check        Run all checks (lint + format + type + length + complexity + test)"
	@echo "  ci           Full CI-equivalent pipeline (all checks + dead-code)"
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

# File length gate (max 500 lines per .py file)
MAX_LINES := 500
file-length:
	@FAILED=0; \
	for f in $$(find src/ -name '*.py'); do \
		count=$$(wc -l < "$$f"); \
		if [ "$$count" -gt $(MAX_LINES) ]; then \
			echo "ERROR: $$f has $$count lines (max $(MAX_LINES))"; \
			FAILED=1; \
		fi; \
	done; \
	if [ "$$FAILED" -eq 1 ]; then \
		echo ""; \
		echo "Files exceeding $(MAX_LINES) lines must be split into smaller modules."; \
		echo "See ARCHITECTURE.md for guidance on module structure."; \
		exit 1; \
	fi; \
	echo "All files under $(MAX_LINES) lines."

# Cyclomatic complexity gate (Xenon grade C)
complexity:
	cd /tmp && uvx xenon --max-absolute C --max-modules D --max-average C $(CURDIR)/src/

# Dead code detection
dead-code:
	uvx vulture src/ --min-confidence 90

# Security lint (Bandit)
security-lint:
	uvx bandit -r src/ --severity-level high -q

# Ensure dev dependencies are installed
ensure-dev:
	@uv sync --all-extras --quiet

# Run all checks (same as CI)
check: ensure-dev lint format-check typecheck file-length complexity test
	@echo "All checks passed!"

# Full CI-equivalent pipeline (locally)
ci: ensure-dev lint format-check typecheck file-length complexity dead-code test
	@echo "Full CI pipeline passed!"

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
