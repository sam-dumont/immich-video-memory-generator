ROOT := ../..
PACKAGE := services/render-worker/immich_memories_render_worker

.PHONY: dev test integration ci coverage package

dev:
	$(MAKE) -C $(ROOT) dev

test:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='services/render-worker/tests' $(MAKE) test

integration:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='-m integration services/render-worker/tests' $(MAKE) test

ci:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='tests services/render-worker/tests' $(MAKE) -f Makefile -f services/render-worker/checks.mk ci SERVICE_TREES=services/render-worker SERVICE_PACKAGES=$(PACKAGE)

coverage:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='--cov-config=services/render-worker/pyproject.toml --cov=$(PACKAGE) --cov=immich_memories.processing.remote_render --cov=immich_memories.processing.remote_render_plan --cov-report=term-missing --cov-report=xml:tests/render-worker-coverage.xml --cov-fail-under=80 -m "not e2e" services/render-worker/tests' $(MAKE) test

package:
	cd $(ROOT) && uv build --wheel services/render-worker --out-dir dist
