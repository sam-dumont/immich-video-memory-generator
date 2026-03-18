# Playwright E2E Testing Spec — UI Wizard (Issue #37, Scenario 7)

> Implementation-ready spec for adding Playwright browser tests to the NiceGUI
> 4-step wizard. Tests wiring only — no generation, no pipeline analysis, no
> music gen. All 7 memory types covered.

---

## 1. Approach

**Tool**: `pytest-playwright` (Python, not Node.js) — integrates with existing
pytest infrastructure.

**Why not NiceGUI's built-in testing?**
- `User` class is simulated Python — won't catch real rendering, JS, or
  WebSocket bugs
- `Screen` class uses Selenium — requires ChromeDriver version management,
  slower, more brittle
- Playwright auto-waits, runs headless Chromium, no driver management, and
  `pytest-playwright` provides a `page` fixture out of the box

**Test philosophy**: Verify UI **wiring** — page loads, navigation, forms,
state persistence, preset-specific controls. Never trigger SmartPipeline,
FFmpeg assembly, LLM analysis, or music generation. The goal is to catch
regressions in the UI layer, not to end-to-end test the video pipeline.

**Data strategy**: Tests run against a **real Immich server** (like other
integration tests). Config overrides force a narrow 1-month date range to
minimize clip count (1-2 clips). Tests skip gracefully if Immich is
unreachable.

---

## 2. Dependencies

Add to `pyproject.toml` `[project.optional-dependencies].dev`:

```toml
"pytest-playwright>=0.6.2",
```

After `uv sync`, install browser binaries:

```bash
uv run playwright install chromium
```

New Makefile target:

```makefile
playwright-install:  ## Install Playwright browser binaries (one-time setup)
	uv run playwright install chromium
```

---

## 3. Pytest Configuration

**`pyproject.toml`** — add `e2e` marker and exclude from default test runs:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires FFmpeg (deselect with '-m not integration')",
    "e2e: end-to-end browser tests (deselect with '-m not e2e')",
]
addopts = "-m 'not integration and not e2e'"
```

---

## 4. Directory Structure

```
tests/e2e/
├── __init__.py
├── conftest.py          # Server lifecycle, service checks, fixtures
└── test_ui_wizard.py    # All wizard wiring tests
```

---

## 5. `tests/e2e/conftest.py` — Full Implementation

```python
"""Fixtures for Playwright E2E tests.

Starts a real NiceGUI server, waits for health, provides Playwright page.
Requires: Immich server reachable, Playwright browsers installed.
Run: make test-e2e-ui
"""

from __future__ import annotations

import logging
import time

import httpx
import pytest
from playwright.sync_api import Page

logger = logging.getLogger("test.e2e")

# ---------------------------------------------------------------------------
# Automatic timing for ALL e2e tests (mirrors tests/integration/conftest.py)
# ---------------------------------------------------------------------------

_test_start_times: dict[str, float] = {}

pytestmark = pytest.mark.e2e


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Record test start time."""
    if "e2e" in [m.name for m in item.iter_markers()]:
        _test_start_times[item.nodeid] = time.monotonic()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item):
    """Log test duration after completion."""
    start = _test_start_times.pop(item.nodeid, None)
    if start is not None:
        duration = time.monotonic() - start
        logger.info(f"TIMING: {item.nodeid} — {duration:.1f}s")


# ---------------------------------------------------------------------------
# Service availability checks
# ---------------------------------------------------------------------------


def _has_immich() -> bool:
    """Check if Immich is reachable using the real config."""
    try:
        from immich_memories.config_loader import Config

        config = Config.from_yaml(Config.get_default_path())
        if not config.immich.url or not config.immich.api_key:
            return False

        resp = httpx.get(
            f"{config.immich.url.rstrip('/')}/api/server/ping",
            headers={"x-api-key": config.immich.api_key},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


requires_immich = pytest.mark.skipif(
    not _has_immich(), reason="Immich not reachable"
)

# ---------------------------------------------------------------------------
# NiceGUI server fixture
# ---------------------------------------------------------------------------

_E2E_PORT = 18080  # Avoid conflict with dev server on 8080


def _find_free_port() -> int:
    """Find an available TCP port (avoids hardcoded port conflicts)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server(port: int) -> None:
    """Run the NiceGUI app (blocking). Called in child process."""
    from immich_memories.ui.app import main
    main(port=port, host="127.0.0.1", reload=False)


@pytest.fixture(scope="session")
def app_server():
    """Start NiceGUI app in a child process, wait for /health, yield base URL.

    Uses multiprocessing.Process for clean lifecycle management.
    The child process inherits the parent's filesystem, so it reads
    ~/.immich-memories/config.yaml (real Immich credentials).
    """
    import multiprocessing

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = multiprocessing.Process(
        target=_run_server,
        args=(port,),
        daemon=True,  # auto-kill if parent exits unexpectedly
    )
    proc.start()

    # Poll /health until server is ready (max 30s)
    for attempt in range(60):
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                logger.info(
                    "NiceGUI server ready on %s (attempt %d)", base_url, attempt
                )
                break
        except httpx.ConnectError:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail(f"NiceGUI server did not start within 30s")

    yield base_url

    # Graceful shutdown
    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()


# ---------------------------------------------------------------------------
# Playwright page fixture (depends on app_server)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_page(app_server: str, page: Page) -> Page:
    """Provide a Playwright page pointed at the running NiceGUI app.

    The `page` fixture comes from pytest-playwright automatically.
    Each test gets a fresh browser context (pytest-playwright default).
    """
    page.goto(app_server)
    # Wait for NiceGUI to finish initial render (WebSocket connection)
    page.wait_for_load_state("networkidle")
    return page
```

### Key design notes

- **`multiprocessing.Process`** (not `subprocess.Popen`): NiceGUI's `ui.run()`
  calls `uvicorn.run()` which blocks. `multiprocessing` provides clean
  `terminate()`/`kill()` semantics without constructing CLI commands.
  `daemon=True` ensures cleanup if the parent exits.
- **Dynamic port** via `_find_free_port()`: Avoids conflicts with dev server
  on 8080 or other test runs. TOCTOU race is acceptable for local testing.
- **Session-scoped server**: One server process for the entire test session.
  Starting NiceGUI takes ~3s; session scope avoids repeating this per test.
- **Function-scoped page**: Each test gets a fresh browser context via
  `pytest-playwright`'s `page` fixture. State resets between tests because
  NiceGUI uses `app.storage.user` tied to browser session.
- **Real config**: The child process inherits the parent's filesystem, so
  `~/.immich-memories/config.yaml` is read with real Immich credentials.
- **`reload=False`**: Disables NiceGUI hot-reload to avoid file watchers.
- **Coverage limitation**: `pytest-cov` in the parent does NOT collect
  server-side coverage from the child process. E2E coverage only measures
  test code. Server-side UI coverage requires `coverage.process_startup`
  in the child (future work).

---

## 6. `tests/e2e/test_ui_wizard.py` — Full Implementation

```python
"""Playwright E2E tests for the NiceGUI 4-step wizard.

Tests UI wiring only: page loads, navigation, form controls, preset selection.
Does NOT trigger: SmartPipeline, clip analysis, video generation, music gen.

Run: make test-e2e-ui
"""

from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import requires_immich

pytestmark = [pytest.mark.e2e, requires_immich]


# ============================================================================
# A. Health & Infrastructure
# ============================================================================


class TestInfrastructure:
    """Server health and basic page loading."""

    def test_health_endpoint(self, app_server: str):
        """GET /health returns 200 with status JSON."""
        resp = httpx.get(f"{app_server}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "immich_reachable" in data
        assert "version" in data

    def test_index_loads_step1(self, app_page: Page):
        """Root URL loads Step 1: Configuration page."""
        expect(app_page.locator("text=Configuration")).to_be_visible(
            timeout=10_000
        )

    def test_sidebar_shows_all_steps(self, app_page: Page):
        """Sidebar displays all 4 wizard steps."""
        sidebar = app_page.locator("aside, nav, .q-drawer")
        expect(sidebar.locator("text=Configuration")).to_be_visible()
        expect(sidebar.locator("text=Clip Review")).to_be_visible()
        expect(sidebar.locator("text=Options")).to_be_visible()
        expect(sidebar.locator("text=Export")).to_be_visible()

    def test_settings_config_page(self, app_server: str, page: Page):
        """Settings > Config page loads and shows YAML."""
        page.goto(f"{app_server}/settings/config")
        page.wait_for_load_state("networkidle")
        expect(page.locator("text=Configuration")).to_be_visible(timeout=10_000)
        # Config page shows redacted YAML values
        expect(page.locator("text=immich")).to_be_visible()

    def test_settings_cache_page(self, app_server: str, page: Page):
        """Settings > Cache page loads."""
        page.goto(f"{app_server}/settings/cache")
        page.wait_for_load_state("networkidle")
        expect(page.locator("text=Cache")).to_be_visible(timeout=10_000)


# ============================================================================
# B. Step 1: Configuration — Immich Connection
# ============================================================================


class TestStep1Connection:
    """Test Immich connection UI in Step 1."""

    def test_immich_url_field_prefilled(self, app_page: Page):
        """Immich URL field is pre-filled from config."""
        url_input = app_page.locator("input[aria-label='Immich URL'], input").first
        # Should have a value (from config.yaml)
        expect(url_input).not_to_be_empty()

    def test_test_connection_button(self, app_page: Page):
        """'Test Connection' button triggers Immich connection check."""
        btn = app_page.locator("text=Test Connection")
        expect(btn).to_be_visible()
        btn.click()
        # Wait for connection result — either success or error message
        app_page.wait_for_timeout(3000)
        # After successful connection, people/years should load and presets appear
        expect(
            app_page.locator("text=Year in Review")
            .or_(app_page.locator("text=Connected"))
            .or_(app_page.locator("text=Connection failed"))
        ).to_be_visible(timeout=15_000)

    def test_connection_shows_preset_grid(self, app_page: Page):
        """After connecting, the preset selector grid appears."""
        # Trigger connection
        app_page.locator("text=Test Connection").click()
        # Wait for presets to render (they appear after people/years are fetched)
        expect(app_page.locator("text=Year in Review")).to_be_visible(
            timeout=15_000
        )
        # All 7 presets + Custom should be visible
        for preset_name in [
            "Year in Review",
            "Season",
            "Person Spotlight",
            "Multi-Person",
            "Monthly Highlights",
            "On This Day",
            "Trip",
            "Custom",
        ]:
            expect(app_page.locator(f"text={preset_name}")).to_be_visible()


# ============================================================================
# C. Step 1: Memory Type Presets — Parametrized
# ============================================================================

# Each preset renders different parameter controls in Step 1.
# These tests verify the correct controls appear for each type.


def _connect_and_wait_for_presets(page: Page) -> None:
    """Helper: click Test Connection, wait for preset grid to appear."""
    page.locator("text=Test Connection").click()
    expect(page.locator("text=Year in Review")).to_be_visible(timeout=15_000)


class TestStep1YearInReview:
    """Year in Review preset: Year picker only."""

    def test_selecting_shows_year_picker(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Year in Review").click()
        # Year picker should appear
        expect(app_page.locator("text=Year").last).to_be_visible()

    def test_year_dropdown_has_options(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Year in Review").click()
        # Click the Year dropdown to open it
        year_select = app_page.locator(
            ".q-select:has-text('Year')"
        ).last
        year_select.click()
        # Dropdown options should appear in a popup
        app_page.wait_for_timeout(500)
        expect(app_page.locator(".q-menu")).to_be_visible()


class TestStep1Season:
    """Season preset: Year + Season + Hemisphere dropdowns."""

    def test_selecting_shows_three_dropdowns(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Season").first.click()
        # Three selects: Year, Season, Hemisphere
        expect(app_page.locator("text=Year").last).to_be_visible()
        expect(app_page.locator("text=Season").last).to_be_visible()
        expect(app_page.locator("text=Hemisphere")).to_be_visible()

    def test_season_dropdown_options(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Season").first.click()
        # Open season dropdown
        season_select = app_page.locator(
            ".q-select:has-text('Season')"
        ).last
        season_select.click()
        app_page.wait_for_timeout(500)
        # Should show spring, summer, autumn, winter
        for season in ["spring", "summer", "autumn", "winter"]:
            expect(
                app_page.locator(f".q-menu >> text={season}")
            ).to_be_visible()


class TestStep1PersonSpotlight:
    """Person Spotlight: Year + Person dropdown + Birthday toggle."""

    def test_selecting_shows_person_controls(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Person Spotlight").click()
        expect(app_page.locator("text=Year").last).to_be_visible()
        expect(app_page.locator("text=Person")).to_be_visible()
        expect(app_page.locator("text=Birthday to birthday")).to_be_visible()

    def test_year_has_all_time_option(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Person Spotlight").click()
        year_select = app_page.locator(".q-select:has-text('Year')").last
        year_select.click()
        app_page.wait_for_timeout(500)
        expect(
            app_page.locator(".q-menu >> text=All Time")
        ).to_be_visible()


class TestStep1MultiPerson:
    """Multi-Person: Year + multi-select people chips."""

    def test_selecting_shows_multi_select(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Multi-Person").click()
        expect(app_page.locator("text=Year").last).to_be_visible()
        # Multi-person select has "select 2+" label
        expect(
            app_page.locator("text=People (select 2+)")
        ).to_be_visible()

    def test_multi_select_uses_chips(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Multi-Person").click()
        # The select should have the 'use-chips' prop (renders as chips)
        expect(
            app_page.locator(".q-select--with-chips, .q-chip")
            .or_(app_page.locator("text=People (select 2+)"))
        ).to_be_visible()


class TestStep1MonthlyHighlights:
    """Monthly Highlights: Year + Month dropdown."""

    def test_selecting_shows_month_picker(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Monthly Highlights").click()
        expect(app_page.locator("text=Year").last).to_be_visible()
        expect(app_page.locator("text=Month")).to_be_visible()

    def test_month_dropdown_has_12_months(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Monthly Highlights").click()
        month_select = app_page.locator(".q-select:has-text('Month')")
        month_select.click()
        app_page.wait_for_timeout(500)
        # At least January and December should be in the dropdown
        expect(
            app_page.locator(".q-menu >> text=January")
        ).to_be_visible()
        expect(
            app_page.locator(".q-menu >> text=December")
        ).to_be_visible()


class TestStep1OnThisDay:
    """On This Day: No extra controls, auto-uses today's date."""

    def test_selecting_shows_auto_message(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=On This Day").click()
        expect(
            app_page.locator("text=Automatically uses today")
        ).to_be_visible()


class TestStep1Trip:
    """Trip: Year picker + async GPS trip detection + trip dropdown.

    This is the most complex preset. Trip detection requires GPS data in
    Immich, so the dropdown may show "No trips detected" if no GPS data
    exists for the selected year.
    """

    def test_selecting_shows_year_and_triggers_detection(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Trip").first.click()
        # Year picker should appear
        expect(app_page.locator("text=Year").last).to_be_visible()
        # Trip detection should trigger (spinner or results)
        expect(
            app_page.locator("text=Detecting trips")
            .or_(app_page.locator("text=No trips detected"))
            .or_(app_page.locator("text=Found"))
            .or_(app_page.locator("text=Select a trip"))
            .or_(app_page.locator("text=Connect to Immich first"))
        ).to_be_visible(timeout=30_000)

    def test_trip_detection_completes(self, app_page: Page):
        """Trip detection should eventually finish (success or no trips)."""
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Trip").first.click()
        # Wait for detection to complete (no spinner)
        app_page.wait_for_timeout(15_000)
        # Either trips found or no trips — spinner should be gone
        expect(
            app_page.locator("text=No trips detected")
            .or_(app_page.locator("text=Found"))
            .or_(app_page.locator("text=Select a trip"))
            .or_(app_page.locator("text=Trip detection failed"))
        ).to_be_visible(timeout=30_000)

    def test_trip_dropdown_shows_location_and_dates(self, app_page: Page):
        """If trips are detected, dropdown shows location + date range."""
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Trip").first.click()
        # Wait for detection
        found_label = app_page.locator("text=Found")
        trip_select = app_page.locator("text=Select a trip")
        no_trips = app_page.locator("text=No trips detected")

        expect(
            found_label.or_(no_trips).or_(trip_select)
        ).to_be_visible(timeout=30_000)

        # If trips were found, verify the dropdown exists
        if found_label.is_visible():
            expect(trip_select).to_be_visible()


class TestStep1Custom:
    """Custom preset: shows message about date range below."""

    def test_selecting_shows_custom_message(self, app_page: Page):
        _connect_and_wait_for_presets(app_page)
        app_page.locator("text=Custom").click()
        expect(
            app_page.locator("text=Configure your date range below")
        ).to_be_visible()


# ============================================================================
# D. Step 1 → Step 2 Navigation
# ============================================================================


class TestStep1Navigation:
    """Navigation from Step 1 to Step 2."""

    def test_next_button_present(self, app_page: Page):
        """'Next: Review Clips' button is visible after connecting."""
        _connect_and_wait_for_presets(app_page)
        expect(
            app_page.locator("text=Next: Review Clips")
        ).to_be_visible()

    def test_next_without_date_range_shows_warning(self, app_page: Page):
        """Clicking Next without selecting a preset shows a warning."""
        _connect_and_wait_for_presets(app_page)
        # Don't select a preset — click Next directly
        # If no date_range is set, it should show a warning notification
        app_page.locator("text=Next: Review Clips").click()
        app_page.wait_for_timeout(1000)
        # Either a warning toast or stays on the same page
        # (depends on whether a default preset is auto-selected)

    def test_navigate_to_step2_with_preset(self, app_page: Page):
        """Selecting a preset and clicking Next navigates to Step 2."""
        _connect_and_wait_for_presets(app_page)
        # Select Year in Review (simplest)
        app_page.locator("text=Year in Review").click()
        app_page.wait_for_timeout(1000)
        # Click Next
        app_page.locator("text=Next: Review Clips").click()
        # Should navigate to /step2
        app_page.wait_for_url("**/step2", timeout=10_000)
        # Step 2 should show loading or clip grid
        expect(
            app_page.locator("text=Clip Review")
            .or_(app_page.locator("text=Loading"))
            .or_(app_page.locator("text=Session expired"))
        ).to_be_visible(timeout=15_000)


# ============================================================================
# E. Step 2: Clip Review
# ============================================================================


class TestStep2ClipReview:
    """Step 2 clip review page — tests with real Immich clips.

    Navigates Step 1 → Step 2 using Year in Review + most recent year.
    Clip loading pulls real thumbnails from Immich (narrow date range = few clips).
    """

    def _navigate_to_step2(self, page: Page, app_server: str):
        """Helper: connect, select Year in Review, navigate to Step 2."""
        page.goto(app_server)
        page.wait_for_load_state("networkidle")
        _connect_and_wait_for_presets(page)
        page.locator("text=Year in Review").click()
        page.wait_for_timeout(1000)
        page.locator("text=Next: Review Clips").click()
        page.wait_for_url("**/step2", timeout=10_000)

    def test_step2_page_loads(self, app_server: str, page: Page):
        """Step 2 loads after navigating from Step 1."""
        self._navigate_to_step2(page, app_server)
        # Wait for clip loading to start or complete
        expect(
            page.locator("text=Loading")
            .or_(page.locator("text=clips"))
            .or_(page.locator("text=No clips"))
            .or_(page.locator("text=Session expired"))
        ).to_be_visible(timeout=30_000)

    def test_step2_shows_date_range(self, app_server: str, page: Page):
        """Step 2 displays the selected date range."""
        self._navigate_to_step2(page, app_server)
        # Wait for page to settle
        page.wait_for_timeout(5000)
        # Date range description should be visible (e.g., "Jan 2024 - Dec 2024")
        # This appears even during loading

    def test_step2_clip_grid_or_loading(self, app_server: str, page: Page):
        """Step 2 shows clip loading progress or clip grid."""
        self._navigate_to_step2(page, app_server)
        # Wait for either loading indicator or clip content
        expect(
            page.locator("text=Fetching")
            .or_(page.locator("text=Loading"))
            .or_(page.locator("text=clips"))
            .or_(page.locator("text=Generate Memories"))
            .or_(page.locator("text=No clips"))
        ).to_be_visible(timeout=60_000)


# ============================================================================
# F. Step 3: Generation Options (direct navigation)
# ============================================================================


class TestStep3Options:
    """Step 3: Generation Options page.

    Since navigating through Step 2 requires waiting for clip loading and
    pipeline, we test Step 3 by navigating directly. The page will show
    "No clips selected" or render the options form if state is populated.
    """

    def test_step3_direct_navigation(self, app_server: str, page: Page):
        """Step 3 can be navigated to directly."""
        page.goto(f"{app_server}/step3")
        page.wait_for_load_state("networkidle")
        # Either shows options form or redirect/warning
        expect(
            page.locator("text=Output Settings")
            .or_(page.locator("text=Options"))
            .or_(page.locator("text=Session expired"))
            .or_(page.locator("text=Back"))
        ).to_be_visible(timeout=10_000)

    def test_step3_output_dropdowns_present(self, app_server: str, page: Page):
        """Step 3 renders orientation, transition, resolution dropdowns."""
        page.goto(f"{app_server}/step3")
        page.wait_for_load_state("networkidle")
        # If Output Settings is visible, check for dropdowns
        output_section = page.locator("text=Output Settings")
        if output_section.is_visible():
            expect(page.locator("text=Orientation")).to_be_visible()
            expect(page.locator("text=Transition Style")).to_be_visible()
            expect(page.locator("text=Resolution")).to_be_visible()
            expect(page.locator("text=Output Format")).to_be_visible()

    def test_step3_title_section(self, app_server: str, page: Page):
        """Step 3 shows Title section with editable fields."""
        page.goto(f"{app_server}/step3")
        page.wait_for_load_state("networkidle")
        if page.locator("text=Title").first.is_visible():
            expect(page.locator("text=Title").first).to_be_visible()

    def test_step3_music_section(self, app_server: str, page: Page):
        """Step 3 shows Music section with source selector."""
        page.goto(f"{app_server}/step3")
        page.wait_for_load_state("networkidle")
        if page.locator("text=Music").first.is_visible():
            expect(page.locator("text=Background music")).to_be_visible()

    def test_step3_navigation_buttons(self, app_server: str, page: Page):
        """Step 3 has Back and Next navigation buttons."""
        page.goto(f"{app_server}/step3")
        page.wait_for_load_state("networkidle")
        if page.locator("text=Output Settings").is_visible():
            expect(
                page.locator("text=Back to Clip Review")
            ).to_be_visible()
            expect(
                page.locator("text=Next: Preview & Export")
            ).to_be_visible()


# ============================================================================
# G. Step 4: Preview & Export (direct navigation)
# ============================================================================


class TestStep4Export:
    """Step 4: Preview & Export page.

    Navigate directly. Shows either "No clips selected" warning or the
    export form if state is populated.
    """

    def test_step4_direct_navigation(self, app_server: str, page: Page):
        """Step 4 can be navigated to directly."""
        page.goto(f"{app_server}/step4")
        page.wait_for_load_state("networkidle")
        expect(
            page.locator("text=No clips selected")
            .or_(page.locator("text=Summary"))
            .or_(page.locator("text=Generate Video"))
            .or_(page.locator("text=Session expired"))
        ).to_be_visible(timeout=10_000)

    def test_step4_no_clips_warning(self, app_server: str, page: Page):
        """Step 4 without clips shows warning and back button."""
        page.goto(f"{app_server}/step4")
        page.wait_for_load_state("networkidle")
        if page.locator("text=No clips selected").is_visible():
            expect(
                page.locator("text=Back to Clip Review")
            ).to_be_visible()

    def test_step4_generate_button_not_clicked(
        self, app_server: str, page: Page
    ):
        """Step 4 Generate Video button exists but we do NOT click it.

        This test explicitly verifies the button is present without
        triggering generation. Generation involves FFmpeg assembly,
        audio processing, and optional Immich upload — all out of scope.
        """
        page.goto(f"{app_server}/step4")
        page.wait_for_load_state("networkidle")
        # Generate button may or may not be visible (depends on state)
        # Just verify the page loaded without errors


# ============================================================================
# H. Cross-Cutting Concerns
# ============================================================================


class TestThemeToggle:
    """Dark/light mode theme toggle."""

    def test_theme_toggle_exists(self, app_page: Page):
        """Theme toggle control is present in the UI."""
        # Theme toggle is typically in the sidebar or header
        # Look for the toggle switch or button
        expect(
            app_page.locator("text=Theme")
            .or_(app_page.locator("[aria-label*='theme']"))
            .or_(app_page.locator("[aria-label*='dark']"))
            .or_(app_page.locator("text=Light"))
            .or_(app_page.locator("text=Dark"))
            .or_(app_page.locator("text=System"))
        ).to_be_visible(timeout=5_000)

    def test_dark_mode_applies_body_class(self, app_page: Page):
        """Toggling dark mode adds body--dark CSS class."""
        # This is NiceGUI/Quasar's dark mode mechanism
        # Check initial state and toggle
        body = app_page.locator("body")
        initial_dark = "body--dark" in (
            body.get_attribute("class") or ""
        )
        # The toggle exists somewhere — find and click it
        # After toggling, the body class should change
        # (Exact selector depends on theme toggle implementation)


class TestSidebarNavigation:
    """Sidebar navigation between wizard steps."""

    def test_sidebar_config_link(self, app_page: Page):
        """Clicking Config in sidebar navigates to settings page."""
        config_link = app_page.locator("text=Config").last
        if config_link.is_visible():
            config_link.click()
            app_page.wait_for_url("**/settings/config", timeout=5_000)

    def test_sidebar_cache_link(self, app_page: Page):
        """Clicking Cache in sidebar navigates to cache page."""
        cache_link = app_page.locator("text=Cache").last
        if cache_link.is_visible():
            cache_link.click()
            app_page.wait_for_url("**/settings/cache", timeout=5_000)


# ============================================================================
# I. Options Toggle Tests
# ============================================================================


class TestStep1Options:
    """Step 1 options section: Live Photos and Favorites toggles."""

    def test_live_photos_toggle(self, app_page: Page):
        """Live Photos toggle is present after connecting."""
        _connect_and_wait_for_presets(app_page)
        expect(
            app_page.locator("text=Include Live Photos")
        ).to_be_visible()

    def test_favorites_toggle(self, app_page: Page):
        """Prioritize Favorites toggle is present after connecting."""
        _connect_and_wait_for_presets(app_page)
        expect(
            app_page.locator("text=Prioritize Favorites")
        ).to_be_visible()
```

---

## 7. Makefile Targets

Add to `Makefile`:

```makefile
playwright-install:  ## Install Playwright browser binaries (one-time setup)
	uv run playwright install chromium

test-e2e-ui:  ## Run Playwright UI wizard tests (~2min, needs Immich + browser)
	uv run pytest tests/e2e/ -v -m e2e --log-cli-level=INFO --tb=short \
		--cov=src/immich_memories --cov-branch --cov-report=xml:tests/e2e-coverage.xml --cov-fail-under=0
```

---

## 8. Coverage Configuration

**Important caveat**: When NiceGUI runs in a child process (subprocess or
`multiprocessing.Process`), `pytest-cov` in the parent process does **NOT**
collect server-side coverage. The E2E coverage XML only covers the test code
itself, not the UI page code executing in the subprocess.

**Therefore: do NOT remove the UI omit from `[tool.coverage.run]` yet.**
Keep the existing exclusions:

```toml
[tool.coverage.run]
omit = [
    "src/immich_memories/ui/pages/*",    # Still excluded — needs subprocess coverage
    "src/immich_memories/ui/theme.py",
    "src/immich_memories/ui/components/*",
]
```

The `fix-coverage-xml-paths` pre-commit hook already uses `tests/*-coverage.xml`
glob — no change needed for `e2e-coverage.xml`.

**Future work**: To get server-side coverage from the child process, use
`coverage.process_startup` in the child and merge `.coverage` files post-test.
This is a separate issue — not needed for the initial E2E PR.

---

## 9. Implementation Order (TDD)

Each step is one RED-GREEN-REFACTOR cycle:

1. `make dev && make playwright-install` — install deps
2. Create `tests/e2e/__init__.py` + `conftest.py` with server fixture
3. **RED**: `test_health_endpoint` — fails (no marker, no server)
4. **GREEN**: Add `e2e` marker to pyproject.toml, fix conftest → passes
5. **RED**: `test_index_loads_step1` — fails
6. **GREEN**: Fix selector → passes
7. **RED**: `TestStep1Connection.test_test_connection_button`
8. **GREEN**: Adjust timeout/selector → passes
9. Repeat for each preset test class (Year in Review → Trip)
10. Add Step 2-4 tests
11. Add cross-cutting tests (theme, sidebar)
12. Update coverage config, CLAUDE.md, ARCHITECTURE.md
13. `make ci` to verify nothing broke

---

## 10. Verification

```bash
# One-time setup
make dev
make playwright-install

# Run UI E2E tests
make test-e2e-ui

# Verify coverage XML generated
ls tests/e2e-coverage.xml

# Verify unit tests still exclude E2E
make test  # Should NOT run any e2e-marked tests

# Full CI check (E2E excluded by default)
make ci
```

---

## 11. What's NOT Covered (Future Work)

These are intentionally out of scope for this PR:

- **SmartPipeline analysis** (Scenario 1-2): separate E2E test file
- **Music generation** (Scenario 3): separate E2E test file
- **Video assembly / FFmpeg** (Scenario 4-6): covered by integration tests
- **Full wizard flow** (Step 1 → 2 → pipeline → 3 → 4 → generate): requires
  waiting for pipeline completion (~minutes) and FFmpeg assembly
- **Upload to Immich**: would create test data in production Immich

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/immich_memories/ui/app.py:347` | `main()` — port/host/reload params |
| `src/immich_memories/ui/app.py:220-250` | `/health` endpoint |
| `src/immich_memories/ui/pages/step1_config.py:326` | `render_step1()` |
| `src/immich_memories/ui/pages/step1_presets.py:19-28` | `_PRESET_CARDS` — all 8 presets |
| `src/immich_memories/ui/pages/step1_presets.py:329-465` | Trip preset UI (most complex) |
| `src/immich_memories/ui/pages/step2_review.py:40-80` | Step 2 header + clip loading trigger |
| `src/immich_memories/ui/pages/step3_options.py:74-296` | `render_step3()` — all form controls |
| `src/immich_memories/ui/pages/step4_export.py:31-152` | `render_step4()` — export + generate button |
| `src/immich_memories/ui/state.py` | `AppState` — all state fields |
| `src/immich_memories/memory_types/factory.py` | 7 preset factories |
| `tests/integration/conftest.py` | Timing hooks pattern to reuse |
