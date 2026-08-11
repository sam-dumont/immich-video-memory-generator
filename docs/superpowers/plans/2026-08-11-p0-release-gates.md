# P0 Version, E2E, CI, Docker, and Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make release identity consistent and replace false-green launch checks with hermetic, required gates and accurate operator documentation.

**Architecture:** Hatch VCS remains the sole runtime/build version source. A local fake Immich v3 server supplies deterministic media to a required Playwright smoke test. Liveness and readiness become distinct endpoints. Package, container, CI, and documentation checks then verify the same launch contract.

**Tech Stack:** Hatch VCS/setuptools-scm, Click, NiceGUI/Starlette, pytest-playwright, FFmpeg/ffprobe, Docker Buildx, GitHub Actions, Docusaurus.

## Global Constraints

- No runtime source file or Docker label hardcodes a release version.
- Wheel metadata, CLI, UI health, and container label report the same build version.
- Required E2E failures are failures, never skips.
- Required E2E uses temporary state and a local fake Immich service; it does not require the user's library.
- The smoke path renders and ffprobes a real tiny video.
- Screenshot generation remains optional and is not allowed to mask required smoke failures.
- `/health/live` answers process liveness; `/health/ready` answers dependency readiness.
- Smart `auto run` is documented as the default scheduler model.
- Immich v2/v3 support, auto detection, and explicit overrides are documented.
- Docker optional features are chosen explicitly; dependency-install failure never silently changes the image feature set.
- The installed user LaunchAgent remains unloaded.
- Every behavior task follows RED → GREEN → REFACTOR.

---

## File structure

- Modify `src/immich_memories/__init__.py`: import generated version.
- Modify `pyproject.toml`: remove conflicting static semantic-release version.
- Modify `docker/Dockerfile`: build-version args/labels, explicit extras, correct home.
- Modify `Makefile`: correct mounts and launch gates.
- Modify `.github/workflows/release.yml`: pass one version into package and image builds.
- Modify `.github/workflows/ci.yml`: run required launch smoke and build checks.
- Create `tests/test_version_contract.py` and `tests/test_docker_contract.py`.
- Create `tests/e2e/fake_immich.py` and `tests/e2e/test_launch_smoke.py`.
- Modify `tests/e2e/conftest.py`, `tests/e2e/test_full_generation.py`, and `tests/e2e/test_screenshots.py`.
- Modify `src/immich_memories/ui/app.py` and `tests/test_health.py`.
- Modify the README and exact docs listed in Task 7.

### Task 1: Use Hatch VCS as the single application version source

**Files:**
- Modify: `src/immich_memories/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_version_contract.py`
- Modify: `tests/test_cli_smoke.py`
- Modify: `tests/test_health.py`

**Interfaces:**
- Consumes: generated `immich_memories._version.__version__`.
- Produces: `immich_memories.__version__` as the same string.
- Produces: CLI `--version` and health JSON using that value.

- [ ] **Step 1: Write cross-surface version tests**

```python
def test_package_exports_generated_version() -> None:
    from immich_memories import __version__
    from immich_memories._version import __version__ as generated
    assert __version__ == generated

def test_cli_reports_package_version(cli_runner) -> None:
    result = cli_runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert immich_memories.__version__ in result.output
```

Read `pyproject.toml` in a test and assert `[tool.hatch.version] source = "vcs"` exists while
`tool.semantic_release.version` and `version_toml` do not.

- [ ] **Step 2: Run version tests**

Run: `uv run pytest tests/test_version_contract.py tests/test_cli_smoke.py tests/test_health.py -q`

Expected: FAIL because `src/immich_memories/__init__.py` and semantic-release still contain
`0.2.0`.

- [ ] **Step 3: Remove static sources**

Import `__version__` from `_version.py` in `__init__.py`. Delete the static `version` and
`version_toml` keys from `[tool.semantic_release]`; release calculation remains in the existing
workflow, while Hatch VCS owns build/runtime identity. Do not edit generated `_version.py` by
hand.

- [ ] **Step 4: Build a wheel with a controlled version**

Run:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION=9.8.7 uv build --wheel
uv run python -c "import zipfile,glob; p=glob.glob('dist/*.whl')[-1]; m=[n for n in zipfile.ZipFile(p).namelist() if n.endswith('METADATA')][0]; print(zipfile.ZipFile(p).read(m).decode())"
```

Expected: wheel metadata contains `Version: 9.8.7`. This build writes only `dist/` and generated
build outputs.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_version_contract.py tests/test_cli_smoke.py tests/test_health.py -q`

Expected: PASS.

```bash
git add src/immich_memories/__init__.py pyproject.toml tests/test_version_contract.py tests/test_cli_smoke.py tests/test_health.py
git commit -m "fix: use VCS version across runtime surfaces"
```

### Task 2: Split process liveness from dependency readiness

**Files:**
- Modify: `src/immich_memories/ui/app.py`
- Modify: `tests/test_health.py`

**Interfaces:**
- Produces: `GET /health/live` with HTTP 200 and `{status: "alive", version}`.
- Produces: `GET /health/ready` with HTTP 200 when ready or 503 when degraded.
- Keeps: `GET /health` compatibility response with the detailed payload and HTTP 200.

- [ ] **Step 1: Write status-code and payload tests**

```python
@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_immich() -> None:
    response = await _liveness_handler(MagicMock())
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "alive"

@pytest.mark.asyncio
async def test_readiness_is_503_when_immich_is_unreachable() -> None:
    with patch("immich_memories.ui.app._check_immich_reachable", return_value=False):
        response = await _readiness_handler(MagicMock())
    assert response.status_code == 503
```

Assert readiness includes detected API version when available, last automation attempt, pending
delivery count, last successful auto run, and version. Secrets must not appear.

- [ ] **Step 2: Run health tests**

Run: `uv run pytest tests/test_health.py tests/test_auth_middleware.py -q`

Expected: FAIL because only `/health` exists and always returns HTTP 200.

- [ ] **Step 3: Implement shared health snapshot plus three handlers**

Build the detailed snapshot once per request. Register all three paths in auth bypass rules.
Liveness performs no network or database work. Readiness uses the detailed snapshot and returns
503 for missing configuration, unreachable Immich, or unsupported detected API major.

- [ ] **Step 4: Run health/auth tests and commit**

Run: `uv run pytest tests/test_health.py tests/test_auth_middleware.py tests/integration/auth/test_oidc_flow.py -q`

Expected: PASS.

```bash
git add src/immich_memories/ui/app.py src/immich_memories/ui/auth.py tests/test_health.py tests/test_auth_middleware.py tests/integration/auth/test_oidc_flow.py
git commit -m "feat: expose honest liveness and readiness"
```

### Task 3: Make Docker builds explicit and versioned

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `Makefile`
- Modify: `.github/workflows/release.yml`
- Create: `tests/test_docker_contract.py`

**Interfaces:**
- Consumes: Docker build args `APP_VERSION` and `INSTALL_EXTRAS`.
- Produces: OCI labels `org.opencontainers.image.version`, `.revision`, and `.source`.
- Uses: `/home/immich/.immich-memories` for persisted user configuration.

- [ ] **Step 1: Write static Docker contract tests**

Read the Dockerfile and Makefile, then assert:

- no `SETUPTOOLS_SCM_PRETEND_VERSION=0.2.0` default;
- no `LABEL version="0.2.0"`;
- no `pip wheel` command joined by `||` fallback;
- healthcheck targets `/health/live`;
- local `docker-run` and `docker-shell` mount to `/home/immich/.immich-memories`;
- both commands mount an output directory writable by UID/GID used in the image.

- [ ] **Step 2: Run Docker contract tests**

Run: `uv run pytest tests/test_docker_contract.py -q`

Expected: FAIL on every listed stale behavior.

- [ ] **Step 3: Implement explicit build inputs**

Use:

```dockerfile
ARG APP_VERSION
ARG INSTALL_EXTRAS=all
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.version=${APP_VERSION}
```

Require non-empty `APP_VERSION` in the build stage. If `INSTALL_EXTRAS=none`, wheel the base
project; otherwise wheel exactly `.[${INSTALL_EXTRAS}]`. Either command fails the build on a
dependency error. Pass release `next_version` and commit SHA as build args in both platform
jobs. For local builds, define
`APP_VERSION ?= $(shell uv run python -c 'from immich_memories._version import __version__; print(__version__)')`
in the Makefile and pass `--build-arg APP_VERSION=$(APP_VERSION)` plus the operator's
`INSTALL_EXTRAS` value.

- [ ] **Step 4: Run static tests and a local image build**

Run:

```bash
uv run pytest tests/test_docker_contract.py -q
make docker INSTALL_EXTRAS=none DOCKER_TAG=launch-check
docker inspect immich-memories:launch-check --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'
```

Expected: tests PASS, build succeeds, label equals the local generated version. The image is not
pushed.

- [ ] **Step 5: Commit container corrections**

```bash
git add docker/Dockerfile Makefile .github/workflows/release.yml tests/test_docker_contract.py
git commit -m "fix: make container features and version explicit"
```

### Task 4: Build a hermetic fake Immich v3 service for browser tests

**Files:**
- Create: `tests/e2e/fake_immich.py`
- Modify: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_fake_immich.py`

**Interfaces:**
- Produces: session fixture `fake_immich_server(tmp_path_factory) -> FakeImmichServer`.
- Produces: deterministic v3 endpoints needed by connection, discovery, download, and upload.
- Produces: generated H.264/SDR source video under the session temporary root.

- [ ] **Step 1: Write fake-service contract tests**

Start the service and use the real `SyncImmichClient(api_version="auto")` to assert:

- `/server/version` resolves v3.1.0;
- `/users/me` returns the test user;
- monthly bucket/search endpoints return two videos and two photos;
- asset duration arrives as integer milliseconds and normalizes to seconds;
- original/playback download returns a valid ffprobe-able file;
- upload accepts the v3 multipart fields, rejects either v2 device field, and records one asset.

- [ ] **Step 2: Run the fake-service tests**

Run: `uv run pytest tests/e2e/test_fake_immich.py -q -m e2e`

Expected: FAIL because no hermetic service exists.

- [ ] **Step 3: Implement the smallest deterministic HTTP service**

Use Python's `ThreadingHTTPServer` in a fixture-owned thread. Generate media once with FFmpeg
lavfi: a 1.2-second 640×360 H.264 color/test-pattern video with a sine audio track, plus two
small JPEGs. Bind port `0` and publish the selected localhost URL. Implement only endpoints
called by the smoke path; unexpected endpoint/method pairs return a JSON 500 containing the
pair so the test exposes missing coverage immediately.

- [ ] **Step 4: Run fake-service and API suites**

Run: `uv run pytest tests/e2e/test_fake_immich.py tests/test_api_compatibility.py tests/test_api_upload.py -q -m 'e2e or not e2e'`

Expected: PASS.

- [ ] **Step 5: Commit the deterministic service**

```bash
git add tests/e2e/fake_immich.py tests/e2e/conftest.py tests/e2e/test_fake_immich.py
git commit -m "test: add hermetic Immich v3 service"
```

### Task 5: Replace skip-based browser smoke with a required real render

**Files:**
- Create: `tests/e2e/test_launch_smoke.py`
- Modify: `tests/e2e/conftest.py`
- Modify: `tests/e2e/test_full_generation.py`
- Modify: `tests/e2e/test_screenshots.py`
- Modify: `Makefile`

**Interfaces:**
- Produces: `launch_app_url(fake_immich_server, tmp_path_factory)` with isolated DB/cache/output.
- Produces: required `test_launch_flow_renders_real_video`.
- Adds: pytest marker `visual` for optional screenshot/library flows.

- [ ] **Step 1: Write the required smoke with hard assertions**

The test must:

1. open Step 1 and assert the app reports Immich v3 connection;
2. select Monthly Highlights and continue;
3. wait for exactly the fake assets to load;
4. run selection and assert at least one selected clip;
5. choose H.264, 720p, hardware disabled, and music disabled;
6. generate the video and require the completion message;
7. locate the output under the fixture's output directory;
8. call `validate_output()` and assert H.264, positive duration, and positive size;
9. assert the fixture database contains exactly one completed manual run and no running rows.

No `try/except Exception`, early `return`, or `pytest.skip` is allowed in this test.

- [ ] **Step 2: Make current startup failures fail and observe RED**

Change `_wait_for_server()` and the generation-server fixture to `pytest.fail()` with the log
tail on early exit/timeout. Set database, cache, and output environment variables for every
spawned E2E app before it starts.

Run: `uv run pytest tests/e2e/test_launch_smoke.py -q -m e2e`

Expected: FAIL until the fake service and exact UI path are wired.

- [ ] **Step 3: Wire the launch server and separate optional visual tests**

Point launch configuration to the fake server and use v3 auto mode. Mark screenshot capture and
external-library full-generation tests `visual`; remove them from required `make e2e`. Keep
`make screenshots` as an explicit operator command. Replace broad screenshot navigation
exceptions with named Playwright timeout catches and diagnostic screenshots, but visual skips
remain allowed when their documented external library prerequisites are absent.

- [ ] **Step 4: Run required and optional test selections**

```bash
make e2e
uv run pytest tests/e2e/test_launch_smoke.py -q -m e2e
uv run pytest tests/e2e/test_screenshots.py --collect-only -q -m visual
```

Expected: required commands PASS with a real probed output and zero skips. Visual collection
finds the screenshot tests without executing them.

- [ ] **Step 5: Commit the required launch flow**

```bash
git add tests/e2e/conftest.py tests/e2e/test_launch_smoke.py tests/e2e/test_full_generation.py tests/e2e/test_screenshots.py Makefile
git commit -m "test: require a real hermetic launch smoke"
```

### Task 6: Put launch gates in CI and package checks

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile`
- Modify: `tests/test_version_contract.py`

**Interfaces:**
- Produces: Make target `launch-check`.
- Requires: unit/static, wheel metadata, docs, hermetic E2E, and Docker contract checks.

- [ ] **Step 1: Add a failing Make-target contract test**

Parse the Make database with `make -qp` and assert `launch-check` depends on `check`, `build`,
`build-check`, `docs-check`, and `e2e`. Assert CI invokes `make launch-check` in a job with
Playwright Chromium and FFmpeg installed.

- [ ] **Step 2: Run the contract test**

Run: `uv run pytest tests/test_version_contract.py tests/test_docker_contract.py -q`

Expected: FAIL because there is no combined launch gate.

- [ ] **Step 3: Add the Make target and CI job**

Add a non-publishing CI job with a 30-minute timeout. Install project dev dependencies,
Playwright Chromium with dependencies, and FFmpeg. Run `make launch-check`; upload E2E server
logs/output probe JSON on failure. Do not add credentials or connect to a real Immich server.

- [ ] **Step 4: Run the local launch gate**

Run: `make launch-check`

Expected: every dependency exits zero. This command builds local artifacts but pushes nothing.

- [ ] **Step 5: Commit release gates**

```bash
git add .github/workflows/ci.yml Makefile tests/test_version_contract.py tests/test_docker_contract.py
git commit -m "ci: require launch readiness gates"
```

### Task 7: Rewrite operator docs around the approved flow

**Files:**
- Modify: `README.md`
- Modify: `docs-site/docs/create/cli/auto.md`
- Modify: `docs-site/docs/create/cli/scheduler.md`
- Modify: `docs-site/docs/create/recipes/automated-generation.md`
- Modify: `docs-site/docs/reference/cli-reference.md`
- Modify: `docs-site/docs/reference/config-reference.md`
- Modify: `docs-site/docs/deploy/configuration/config-file.md`
- Modify: `docs-site/docs/deploy/configuration/environment-variables.md`
- Modify: `docs-site/docs/deploy/installation/docker.md`
- Modify: `docs-site/docs/deploy/maintenance/health-logs-cache.md`
- Modify: `docs-site/docs/deploy/maintenance/upgrading.md`
- Modify: `docs-site/docs/reference/troubleshooting.md`
- Modify: `docs-site/src/pages/index.tsx`
- Create: `tests/test_docs_contract.py`

**Interfaces:**
- Documents: canonical daily command, typed outcomes/exits, variety rules, delivery retry, API policy, codecs, health, Docker mounts, and legacy scheduler status.

- [ ] **Step 1: Add documentation assertions**

Create `tests/test_docs_contract.py` to assert:

- README names Immich v2 and v3, not only v2.5.6;
- config reference includes `api_version: auto`, `v2`, and `v3`;
- auto docs say latest completed month only, monthly max once/calendar month, no same category
  twice, category max two of six, and one action/day;
- automated-generation docs call `auto run` the recommended single daily entry point;
- scheduler docs label the explicit cron daemon advanced/legacy and link to auto;
- health docs distinguish `/health/live` from `/health/ready` and status codes;
- Docker docs mount `/home/immich/.immich-memories` and explain explicit extras;
- upgrading docs include v2→v3 duration/upload compatibility and the override escape hatch.

- [ ] **Step 2: Run docs contract and build**

Run: `uv run pytest tests/test_docs_contract.py -q && make docs-check`

Expected: FAIL on stale support, scheduler, health, and mount text.

- [ ] **Step 3: Update docs in the repository's direct voice**

State the behavior with exact commands and examples. Include this configuration:

```yaml
immich:
  url: https://photos.example.com
  api_key: ${IMMICH_API_KEY}
  api_version: auto  # auto | v2 | v3
```

Explain that auto means runtime detection; the explicit values are troubleshooting/manual
overrides, not a request for the user to choose a version on every run. Replace the homepage's
“built-in cron scheduler” claim with the daily smart decision model. Do not claim that the
paused local LaunchAgent has been reactivated.

- [ ] **Step 4: Generate CLI reference and build docs**

```bash
make docs-cli
make docs-check
uv run pytest tests/test_docs_contract.py -q
```

Expected: generated CLI docs match current help and the Docusaurus build exits zero.

- [ ] **Step 5: Commit launch documentation**

```bash
git add README.md docs-site/docs/create/cli/auto.md docs-site/docs/create/cli/scheduler.md docs-site/docs/create/recipes/automated-generation.md docs-site/docs/reference/cli-reference.md docs-site/docs/reference/config-reference.md docs-site/docs/deploy/configuration/config-file.md docs-site/docs/deploy/configuration/environment-variables.md docs-site/docs/deploy/installation/docker.md docs-site/docs/deploy/maintenance/health-logs-cache.md docs-site/docs/deploy/maintenance/upgrading.md docs-site/docs/reference/troubleshooting.md docs-site/src/pages/index.tsx tests/test_docs_contract.py
git commit -m "docs: explain the launch automation contract"
```

### Task 8: Run the complete P0 program gate

**Files:**
- Modify only through the owning task when verification exposes a defect.

**Interfaces:**
- Produces: launch-candidate evidence without changing external scheduler state.

- [ ] **Step 1: Run the full automated gate**

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run lint-imports
make e2e
make build
make build-check
make docs-check
```

Expected: every command exits zero; required E2E reports zero skips and probes a real output.

- [ ] **Step 2: Inspect version consistency**

```bash
uv run immich-memories --version
uv run python -c "from immich_memories import __version__; print(__version__)"
uv run python -c "import glob,zipfile; p=glob.glob('dist/*.whl')[-1]; m=[n for n in zipfile.ZipFile(p).namelist() if n.endswith('METADATA')][0]; print([line for line in zipfile.ZipFile(p).read(m).decode().splitlines() if line.startswith('Version:')][0])"
```

Expected: all three normalized version strings match.

- [ ] **Step 3: Confirm the scheduler is still not loaded**

Run: `launchctl print gui/$(id -u)/com.immich-memories.auto`

Expected: nonzero with “Could not find service”. The preserved plist remains at
`/Users/sam/Library/LaunchAgents/com.immich-memories.auto.plist`; do not load it.

- [ ] **Step 4: Route corrections through RED tests**

If verification exposes a defect, return to the owning task, add the smallest failing
regression test, and use that task's explicit commit. Do not create a catch-all commit.
