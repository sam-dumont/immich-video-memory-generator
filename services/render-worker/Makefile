ROOT := ../..
PACKAGE := services/render-worker/immich_memories_render_worker

.PHONY: dev test integration ci

dev:
	$(MAKE) -C $(ROOT) dev

test:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='services/render-worker/tests' $(MAKE) test

integration:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='-m integration services/render-worker/tests' $(MAKE) test

ci:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='tests services/render-worker/tests' $(MAKE) -f Makefile -f services/render-worker/checks.mk ci SERVICE_TREES=services/render-worker SERVICE_PACKAGES=$(PACKAGE)

coverage:
	cd $(ROOT) && PYTHONPATH=services/render-worker PYTEST_ADDOPTS='--cov-config=services/render-worker/pyproject.toml --cov=$(PACKAGE) --cov-report=term-missing --cov-report=xml:coverage.xml --cov-fail-under=80 -m "not e2e" services/render-worker/tests' $(MAKE) test
