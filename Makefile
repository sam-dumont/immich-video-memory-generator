# Makefile for immich-memories
# Uses uv for fast Python package management
export PYTHONUNBUFFERED=1

.PHONY: help install dev dev-ci dev-test run preflight test test-cov test-cov-xml test-integration test-integration-auth test-integration-photos test-integration-audio test-fast mutation benchmark benchmark-perf lint format typecheck check clean clean-cache clean-all build build-check docker docker-run file-length complexity cognitive-complexity security-lint bandit-ci semgrep dead-code duplication refurb dep-check arch-check diff-cover diff-cover-ci ci critique ensure-dev commitlint pip-audit docs-install docs-dev docs-build docs-check docs-cli demo-video playwright-install e2e e2e-full screenshots diagrams

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
	@echo "  benchmark-perf Run assembly performance benchmarks (requires FFmpeg)"
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
	@echo "E2E Tests (Playwright):"
	@echo "  playwright-install  Install Playwright browsers"
	@echo "  e2e                 Run Playwright E2E tests (fast)"
	@echo "  e2e-full            Run ALL E2E tests + full generation (~10min)"
	@echo "  screenshots         Capture UI screenshots (light + dark)"
	@echo "  diagrams            Render architecture diagrams (Mermaid)"
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

# Install dev + GPU extras for CI test jobs (taichi/freetype, no torch/nvidia)
dev-test:
	uv sync --extra dev --extra gpu

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

benchmark-perf:  ## Run assembly performance benchmarks (requires FFmpeg)
	uv run pytest tests/integration/assembly/test_perf_assembly.py -v -m integration \
		--log-cli-level=INFO --tb=short

test-integration-live-photos:  ## Run ONLY live photo merge tests (~30s, needs Immich)
	uv run pytest tests/integration/live_photos/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/live-photos-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/live-photos-junit.xml

test-integration-photos:  ## Run ONLY photo animation tests (~20s, FFmpeg only)
	uv run pytest tests/integration/photos/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/photos-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/photos-junit.xml

test-integration-assembly:  ## Run ONLY FFmpeg assembly tests (~10s, no Immich needed)
	uv run pytest tests/integration/assembly/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/assembly-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/assembly-junit.xml

test-integration-pipeline:  ## Run ONLY pipeline tests (~60s, needs Immich + FFmpeg)
	uv run pytest tests/integration/pipeline/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/pipeline-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/pipeline-junit.xml

test-integration-cli:  ## Run ONLY CLI generate tests (SLOW ~15min, needs Immich + full pipeline)
	uv run pytest tests/integration/cli/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/cli-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/cli-junit.xml

test-integration-auth:  ## Run ONLY auth integration tests (~10s, no external deps)
	uv run pytest tests/integration/auth/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/auth-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/auth-junit.xml

test-integration-audio:  ## Run ONLY audio ML tests (~2min, needs demucs/acestep packages)
	uv run pytest tests/integration/audio/ -v -m integration --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/audio-coverage.xml --cov-fail-under=0 \
		--junitxml=tests/audio-junit.xml

test-integration:  ## Run ALL integration tests per-suite (requires FFmpeg/Immich), saves per-suite coverage XMLs
	$(MAKE) test-integration-auth
	$(MAKE) test-integration-assembly
	$(MAKE) test-integration-photos
	$(MAKE) test-integration-pipeline
	$(MAKE) test-integration-live-photos
	@# CLI tests excluded — they re-run the full pipeline (~41 min) which is
	@# already covered by test-integration-pipeline. Run separately: make test-integration-cli
	@# Merge per-suite JUnit XMLs into one (no re-run needed)
	@python3 scripts/merge_junit_xml.py tests/integration-junit.xml \
		tests/auth-junit.xml tests/assembly-junit.xml tests/photos-junit.xml \
		tests/pipeline-junit.xml tests/live-photos-junit.xml tests/audio-junit.xml 2>/dev/null || true
	@echo ""
	@echo "═══════════════════════════════════════════════════"
	@echo "  INTEGRATION TEST PERFORMANCE REPORT"
	@echo "═══════════════════════════════════════════════════"
	@python3 -c "\
	import xml.etree.ElementTree as ET; \
	root = ET.parse('tests/integration-junit.xml').getroot(); \
	suite = root.find('.//testsuite'); \
	total = float(suite.get('time', 0)); \
	tests = suite.findall('testcase'); \
	tests.sort(key=lambda t: float(t.get('time', 0)), reverse=True); \
	print(f'  Total: {total:.0f}s ({total/60:.1f} min)'); \
	print(f'  Tests: {len(tests)}'); \
	print(''); \
	print('  Slowest tests:'); \
	[print(f'    {float(t.get(\"time\",0)):>7.1f}s  {t.get(\"name\")}') for t in tests[:5]]; \
	print(''); \
	print('  All tests:'); \
	[print(f'    {float(t.get(\"time\",0)):>7.1f}s  {t.get(\"classname\").split(\".\")[-1]}::{t.get(\"name\")}') for t in tests]; \
	print('═══════════════════════════════════════════════════')"

# =============================================================================
# E2E Tests (Playwright)
# =============================================================================

playwright-install:  ## Install Playwright browsers for E2E tests
	uv run playwright install chromium

e2e:  ## Run Playwright E2E tests (fast — screenshots + auth, ~3min)
	uv run pytest tests/e2e/ -v -m "e2e and not slow" --log-cli-level=INFO --tb=short \
		--junitxml=tests/e2e-junit.xml

e2e-full:  ## Run ALL E2E tests including full generation pipeline (~10min)
	uv run pytest tests/e2e/ -v -m e2e --log-cli-level=INFO --tb=short \
		--junitxml=tests/e2e-junit.xml

screenshots:  ## Capture UI screenshots in light + dark mode (coverage from server subprocess)
	uv run pytest tests/e2e/test_screenshots.py -v -m e2e --log-cli-level=INFO --tb=short \
		--junitxml=tests/e2e-junit.xml

diagrams:  ## Render architecture diagrams from Mermaid source files
	@for f in docs-site/diagrams/setup-*.mmd; do \
		name=$$(basename "$$f" .mmd); \
		echo "Rendering $$name (dark + light)..."; \
		npx --yes @mermaid-js/mermaid-cli -i "$$f" -o "docs-site/static/img/diagrams/$${name}.png" -w 800 -H 400 -b transparent -c docs-site/diagrams/mermaid-config.json 2>/dev/null; \
		npx --yes @mermaid-js/mermaid-cli -i "$$f" -o "docs-site/static/img/diagrams/$${name}-light.png" -w 800 -H 400 -b transparent -c docs-site/diagrams/mermaid-config-light.json 2>/dev/null; \
	done
	@echo "Diagrams saved to docs-site/static/img/diagrams/"

mutation:  ## Run mutation testing (slow — weekly CI or local deep validation)
	uv run mutmut run --max-children 4
	uv run mutmut results

test-cov:
	uv run pytest --cov=src/immich_memories --cov-report=html --cov-report=term-missing
	@echo "Coverage report: htmlcov/index.html"

test-cov-xml:  ## Run tests with XML coverage + JUnit results (for CI upload)
	uv run pytest --cov=src/immich_memories --cov-branch --cov-report=xml --junitxml=junit.xml -o junit_family=legacy -v

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
	uvx --from commitizen cz check --rev-range $${COMMIT_RANGE:-HEAD~1..HEAD}

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
	uvx diff-cover coverage.xml --compare-branch=origin/main --fail-under=80

# Dependency vulnerability audit
pip-audit:
	uv pip freeze | grep -v -e '^-e ' -e '^immich-memories==' -e '^audioop-lts==' > /tmp/pip-audit-reqs.txt
	uvx pip-audit -r /tmp/pip-audit-reqs.txt --strict
	rm -f /tmp/pip-audit-reqs.txt

diff-cover-local:  ## Check diff-cover locally before pushing (runs tests + merges integration coverage)
	@echo "Running unit tests with coverage..."
	@uv run pytest --cov=src/immich_memories --cov-branch --cov-report=xml -q
	@echo "Checking diff coverage against main..."
	@COVERAGE_FILES="coverage.xml"; \
	for f in tests/*-coverage.xml; do \
		if [ -f "$$f" ]; then COVERAGE_FILES="$$COVERAGE_FILES $$f"; fi; \
	done; \
	uvx diff-cover $$COVERAGE_FILES --compare-branch=origin/main --fail-under=80 \
	|| (echo "" && echo "⚠️  Diff coverage below 80%." && echo "   Run: make test-integration" && echo "   Then: git add tests/*-coverage.xml" && exit 1)

# Diff coverage for PRs — merges CI unit coverage with local integration coverage.
# If coverage is low, run `make test-integration` locally and commit the updated
# per-suite coverage XMLs (tests/*-coverage.xml) before pushing.
diff-cover-ci:
	@SRC_CHANGED=$$(git diff --numstat origin/main...HEAD -- '*.py' 2>/dev/null | grep '^' | grep -v 'tests/' | awk '{s+=$$1+$$2} END {print s+0}'); \
	echo "Changed source lines (excl tests): $${SRC_CHANGED}"; \
	if [ "$${SRC_CHANGED}" -gt 1000 ]; then \
		echo "WARN: Skipping diff-cover: $${SRC_CHANGED} lines changed (>1000). Large refactor."; \
	elif [ "$${SRC_CHANGED}" -lt 10 ]; then \
		echo "WARN: Skipping diff-cover: $${SRC_CHANGED} source lines changed (<10). Too few for meaningful threshold."; \
	else \
		COVERAGE_FILES="coverage.xml"; \
		for f in tests/*-coverage.xml; do \
			if [ -f "$$f" ]; then \
				COVERAGE_FILES="$$COVERAGE_FILES $$f"; \
			fi; \
		done; \
		if [ "$$COVERAGE_FILES" != "coverage.xml" ]; then \
			echo "Merging local integration coverage with CI coverage"; \
		fi; \
		uvx diff-cover $$COVERAGE_FILES --compare-branch=origin/main --fail-under=80; \
	fi

# Build check (twine)
build-check:
	uvx twine check dist/*

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
	@echo "Checking test quality (mock ratios, mock-only assertions, excessive patches)..."
	@uv run python scripts/critique_tests.py
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
