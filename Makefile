# Makefile for immich-memories
# Uses uv for fast Python package management


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
	@echo "  benchmark    Run performance benchmarks"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint         Run ruff linter"
	@echo "  format       Format code with ruff"
	@echo "  typecheck    Run mypy type checker"
	@echo "  file-length  Check all .py files are ≤800 lines"
	@echo "  complexity   Check cyclomatic complexity (Xenon grade C)"
	@echo "  dead-code    Detect dead code (Vulture)"
	@echo "  security-lint Run Bandit security linter"
	@echo "  commitlint   Validate commit messages (conventional commits)"
	@echo "  pip-audit    Check dependencies for known vulnerabilities"
	@echo "  check        Run all checks (lint + format + type + length + complexity + test)"
	@echo "  ci           Full CI pipeline (check + dead-code + security-lint)"
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
	@echo "Documentation:"
	@echo "  docs-install Install docs site dependencies"
	@echo "  docs-dev     Start docs dev server"
	@echo "  docs-build   Build docs site for production"
	@echo "  docs-check   Validate docs build (used in CI)"
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

# Install dev tools only (no GPU/CUDA/audio-ml/face deps — for CI quality gates)
dev-ci:
	uv sync --extra dev

# Install with macOS-specific extras (Apple Vision, Metal GPU, etc.)
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

benchmark:
	uv run pytest tests/benchmarks/ -v --benchmark-only

test-integration:  ## Run integration tests (requires FFmpeg)
	uv run pytest tests/integration/ -v -m integration

mutation:  ## Run mutation testing (slow — weekly CI or local deep validation)
	uv run mutmut run --max-children 4
	uv run mutmut results

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

# File length gate: 800 soft (warning), 1000 hard (error)
SOFT_LINES := 800
HARD_LINES := 1000
file-length:
	@FAILED=0; \
	for f in $$(find src/ -name '*.py'); do \
		count=$$(wc -l < "$$f"); \
		if [ "$$count" -gt $(HARD_LINES) ]; then \
			echo "ERROR: $$f has $$count lines (hard limit $(HARD_LINES))"; \
			FAILED=1; \
		elif [ "$$count" -gt $(SOFT_LINES) ]; then \
			echo "WARN:  $$f has $$count lines (soft limit $(SOFT_LINES) — consider splitting)"; \
		fi; \
	done; \
	if [ "$$FAILED" -eq 1 ]; then \
		echo ""; \
		echo "Files exceeding $(HARD_LINES) lines MUST be split."; \
		echo "Files over $(SOFT_LINES) lines SHOULD be split when practical."; \
		exit 1; \
	fi; \
	echo "All files under $(HARD_LINES) lines (hard limit)."

# Cyclomatic complexity gate (Xenon grade C)
complexity:
	cd /tmp && uvx xenon --max-absolute C --max-modules D --max-average C $(CURDIR)/src/

# Dead code detection
dead-code:
	uvx vulture src/ --min-confidence 90

# Security lint (Bandit)
security-lint:
	uvx bandit -r src/ --severity-level high -q

# Bandit with JSON report and HIGH severity gate (for CI)
bandit-ci:
	@uvx bandit -r src/ --severity-level medium -f json -o bandit-report.json || true
	@python3 -c "import json,sys; r=json.load(open('bandit-report.json')); \
	high=[i for i in r['results'] if i['issue_severity']=='HIGH']; \
	[print(f\"  {i['filename']}:{i['line_number']} [{i['test_id']}] {i['issue_text']}\") for i in high]; \
	sys.exit(len(high))"

# Commit message lint (Commitizen conventional commits)
commitlint:
	uvx --from commitizen cz check --rev-range HEAD~1..HEAD

# Cognitive complexity (complements cyclomatic complexity)
cognitive-complexity:
	@OUTPUT=$$(uvx complexipy src/ --max-complexity-allowed 15 2>&1); \
	if echo "$$OUTPUT" | grep -q "Snapshot watermark passed"; then \
		echo "Cognitive complexity: snapshot watermark passed (no new violations)"; \
	elif echo "$$OUTPUT" | grep -q "FAILED"; then \
		echo "$$OUTPUT" | grep "FAILED"; \
		echo "Cognitive complexity gate FAILED: new violations detected"; \
		exit 1; \
	else \
		echo "Cognitive complexity: all functions under threshold"; \
	fi

# Code duplication detection
duplication:
	npx jscpd src/ --threshold 5 --min-lines 5 --min-tokens 50 --format python --gitignore

# Modernization lint (cd src avoids duplicate module detection, --config-file reads ignores)
refurb:
	cd src && uv run refurb immich_memories/ --quiet --config-file ../pyproject.toml

# Semgrep SAST (cross-file security analysis)
# Excludes sqlalchemy-execute-raw-query: our SQL uses ?-parameterized values,
# column names are hardcoded in code. Semgrep can't distinguish f-string placeholders from injection.
semgrep:
	uvx semgrep scan --config auto --config p/python --error --severity ERROR \
		--exclude-rule python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query \
		src/

# Dependency hygiene (hallucinated/unused/transitive deps)
dep-check:
	uvx deptry src/

# Docstring coverage for public API

# Architectural boundary enforcement
arch-check:
	uv run lint-imports

# Diff coverage (for PRs: new code must be ≥95% covered)
diff-cover:
	uv run pytest --cov=src/immich_memories --cov-branch --cov-report=xml -q
	uvx diff-cover coverage.xml --compare-branch=origin/main --fail-under=95

# Dependency vulnerability audit
pip-audit:
	uv pip freeze | grep -v -e '^-e ' -e '^immich-memories==' -e '^audioop-lts==' > /tmp/pip-audit-reqs.txt
	uvx pip-audit -r /tmp/pip-audit-reqs.txt --strict
	rm -f /tmp/pip-audit-reqs.txt

# Ensure dev dependencies are installed
ensure-dev:
	@uv sync --all-extras --quiet

# Run all checks (same as CI)
check: ensure-dev lint format-check typecheck file-length complexity test
	@echo "All checks passed!"

# Full CI-equivalent pipeline (locally)
ci: ensure-dev lint format-check typecheck file-length complexity cognitive-complexity dead-code security-lint refurb dep-check arch-check duplication critique test
	@echo "Full CI pipeline passed!"

# Self-critique for AI code smells
critique:  ## Run self-critique checks for AI code smells
	@echo "=== AI Smell Audit ==="
	@echo "Checking for remaining mixins..."
	@MIXINS=$$(grep -rn "class.*Mixin" src/ --include="*.py" | grep -v __pycache__ | wc -l | tr -d ' '); \
	if [ "$$MIXINS" -gt 0 ]; then \
		grep -rn "class.*Mixin" src/ --include="*.py" | grep -v __pycache__; \
		echo "FAIL: $$MIXINS mixin classes found — use composition"; \
		exit 1; \
	fi
	@echo "Checking for mechanical split comments..."
	@! grep -rn "to stay within\|to keep.*under.*line\|keep files under" src/ --include="*.py" || (echo "FAIL: Fix these splits" && exit 1)
	@echo "Checking for wildcard re-exports (outside __init__)..."
	@! grep -rn "from .* import \*" src/ --include="*.py" | grep -v __init__ || (echo "WARN: Wildcard re-exports found" && exit 1)
	@echo "Checking for mock-heavy tests (>5 mocks per test)..."
	@python3 -c "import pathlib, re; \
	files = list(pathlib.Path('tests').rglob('test_*.py')); \
	[print(f'HIGH-MOCK: {f}:{i+1}') for f in files if '__pycache__' not in str(f) \
	for i, line in enumerate(f.read_text().splitlines()) \
	if 'Mock()' in line or '@patch' in line]" 2>/dev/null | head -20 || true
	@echo "Self-critique complete."

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

clean-preview-cache:
	rm -rf ~/.immich-memories/cache/preview-cache
	@echo "Preview cache cleared"

clean-all-cache: clean-cache clean-video-cache clean-thumbnail-cache clean-preview-cache
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

# =============================================================================
# Documentation (Docusaurus)
# =============================================================================

docs-cli:
	uv run python scripts/generate_cli_docs.py

docs-install:
	cd docs-site && npm ci

docs-dev:
	cd docs-site && npm start

docs-build:
	cd docs-site && npm run build

docs-check:
	@cd docs-site && npm run build 2>&1 | tee /tmp/docs-build.log; \
	if grep -qiE '(error|broken link)' /tmp/docs-build.log; then \
		echo "Docs build has errors — see output above"; \
		exit 1; \
	fi; \
	echo "Docs build passed."

# Record and assemble a product demo video from the live UI
demo-video: docs-install
	@echo "Recording UI demo (requires running UI on port 8099)..."
	cd docs-site && npx tsx scripts/record-demo.ts
	@echo "Assembling final video..."
	cd docs-site && bash scripts/assemble-demo.sh
	@echo "Demo video saved to docs-site/static/demo/demo.mp4"
