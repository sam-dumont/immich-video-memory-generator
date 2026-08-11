"""Playwright E2E test fixtures.

Run locally with: make e2e  (or make screenshots for screenshot capture only)
Requires either a running UI server on :8099 or auto-starts one.

When auto-starting, auth is disabled via IMMICH_MEMORIES_AUTH__ENABLED=false
and the server runs under `coverage run` so UI code coverage is tracked.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import yaml
from playwright.sync_api import Page

from tests.e2e.fake_immich import FakeImmichServer

_BASE_PORT = 8099
_BASE_URL = f"http://localhost:{_BASE_PORT}"
_STARTUP_TIMEOUT = 30  # seconds
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_COVERAGE_FILE = _REPO_ROOT / ".coverage.e2e-server"


def pytest_configure(config: pytest.Config) -> None:
    """Register the opt-in marker owned by browser artifact tests."""
    config.addinivalue_line(
        "markers",
        "visual: optional screenshots or external-library browser flows",
    )


@dataclass(frozen=True, slots=True)
class LaunchWorkspace:
    """All disposable state owned by the required launch smoke."""

    root: Path
    config_path: Path
    database_path: Path
    cache_dir: Path
    output_dir: Path
    log_path: Path


@pytest.fixture(scope="session")
def launch_workspace(
    tmp_path_factory: pytest.TempPathFactory,
    fake_immich_server: FakeImmichServer,
) -> LaunchWorkspace:
    """Create one config whose mutable paths stay under a pytest temp root."""
    root = tmp_path_factory.mktemp("launch-smoke")
    database_path = root / "cache" / "launch.db"
    cache_dir = root / "cache"
    output_dir = root / "output"
    config_path = root / "config.yaml"
    log_path = root / "server.log"
    cache_dir.mkdir()
    output_dir.mkdir()
    config_path.write_text(
        yaml.safe_dump(
            {
                "immich": {
                    "url": fake_immich_server.base_url,
                    "api_key": fake_immich_server.api_key,
                    "api_version": "auto",
                },
                "output": {
                    "directory": str(output_dir),
                    "format": "mp4",
                    "resolution": "720p",
                    "codec": "h264",
                    "hdr_mode": "sdr",
                    "quality": "low",
                },
                "cache": {
                    "directory": str(cache_dir),
                    "database": str(database_path),
                    "video_cache_enabled": True,
                    "video_cache_max_size_gb": 1,
                    "video_cache_max_age_days": 1,
                },
                "upload": {"enabled": False},
                "photos": {"enabled": True},
                "advanced": {
                    "hardware": {
                        "enabled": False,
                        "backend": "none",
                        "gpu_analysis": False,
                        "gpu_decode": False,
                    },
                    "musicgen": {"enabled": False},
                    "ace_step": {"enabled": False},
                    "content_analysis": {"enabled": False},
                    "audio_content": {"enabled": False},
                },
            },
            sort_keys=False,
        )
    )
    return LaunchWorkspace(
        root=root,
        config_path=config_path,
        database_path=database_path,
        cache_dir=cache_dir,
        output_dir=output_dir,
        log_path=log_path,
    )


_LAUNCH_BOOTSTRAP = """
import sys
from pathlib import Path

import immich_memories.config_loader as config_loader

config_path = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
config_loader.Config.get_default_path = classmethod(lambda cls: config_path)
config_loader.init_config_dir = lambda: state_dir

from immich_memories.ui.app import main

main(port=int(sys.argv[3]), host="127.0.0.1", reload=False)
"""

_PRODUCTION_SHORTCUT_ENV = frozenset(
    {
        "IMMICH_URL",
        "IMMICH_API_KEY",
        "OPENAI_API_KEY",
        "MUSICGEN_ENABLED",
        "MUSICGEN_BASE_URL",
        "MUSICGEN_API_KEY",
        "ACE_STEP_ENABLED",
        "ACE_STEP_MODE",
        "ACE_STEP_API_URL",
    }
)


def _build_launch_environment() -> dict[str, str]:
    """Return a subprocess environment isolated from every provider override."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("IMMICH_MEMORIES_", "NICEGUI_"))
        and key not in _PRODUCTION_SHORTCUT_ENV
        and key != "PYTEST_CURRENT_TEST"
    }
    env.update(
        {
            "IMMICH_MEMORIES_AUTH__ENABLED": "false",
            "IMMICH_MEMORIES_STORAGE_SECRET": "launch-smoke-storage-secret",
            "ENABLE_TAICHI_HEADER_PRINT": "0",
            "TI_LOG_LEVEL": "error",
        }
    )
    return env


@pytest.fixture(scope="session")
def launch_app_url(
    launch_workspace: LaunchWorkspace,
    unused_tcp_port_factory,
) -> Generator[str, None, None]:
    """Run the app against only the fake service and disposable local state."""
    port = unused_tcp_port_factory()
    url = f"http://127.0.0.1:{port}"
    env = _build_launch_environment()

    venv_python = _REPO_ROOT / ".venv" / "bin" / "python"
    with launch_workspace.log_path.open("w") as log_file:
        proc = subprocess.Popen(
            [
                str(venv_python),
                "-c",
                _LAUNCH_BOOTSTRAP,
                str(launch_workspace.config_path),
                str(launch_workspace.root / "state"),
                str(port),
            ],
            stdout=log_file,
            stderr=log_file,
            cwd=_REPO_ROOT,
            env=env,
        )
        try:
            _wait_for_server(
                proc,
                base_url=url,
                log_path=launch_workspace.log_path,
            )
            yield url
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


@pytest.fixture(scope="session")
def fake_immich_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[FakeImmichServer]:
    """Run the deterministic Immich v3 test service for this test session."""
    server = FakeImmichServer.start(tmp_path_factory.mktemp("fake-immich"))
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(scope="session")
def app_url() -> Generator[str, None, None]:
    """Yield the base URL of a running UI server.

    Reuses an existing server if one is already listening on :8099,
    otherwise starts one under `coverage run` (with auth disabled)
    and tears it down after the session.
    """
    if _server_is_ready():
        yield _BASE_URL
        return

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("NICEGUI_") and k != "PYTEST_CURRENT_TEST"
    }
    env["IMMICH_MEMORIES_AUTH__ENABLED"] = "false"
    # Don't enable demo mode via config — we inject the CSS class directly
    # in enable_demo_mode(). The toggle would show in screenshots otherwise.
    env["COVERAGE_FILE"] = str(_SERVER_COVERAGE_FILE)

    venv_bin = _REPO_ROOT / ".venv" / "bin"
    proc = subprocess.Popen(
        [
            str(venv_bin / "coverage"),
            "run",
            "--source=immich_memories",
            "--branch",
            str(venv_bin / "immich-memories"),
            "ui",
            "--port",
            str(_BASE_PORT),
            "--host",
            "localhost",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _wait_for_server(proc)
        yield _BASE_URL
    finally:
        # WHY: SIGINT (not SIGTERM) — uvicorn handles SIGINT gracefully and
        # runs atexit hooks, which is how coverage writes its data file.
        # SIGTERM skips atexit in uvicorn.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        _convert_server_coverage()


@pytest.fixture(scope="session")
def browser_context_args() -> dict:
    """Override pytest-playwright default viewport to match screenshot size."""
    return {"viewport": {"width": 1440, "height": 900}}


@pytest.fixture(scope="session")
def demo_raw_dir() -> Path:
    """Directory for raw demo video recordings."""
    out = _REPO_ROOT / "docs-site" / "static" / "demo" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="session")
def screenshot_dir() -> Path:
    """Path to the docs-site screenshot directory."""
    repo_root = Path(__file__).resolve().parents[2]
    out = repo_root / "docs-site" / "static" / "img" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    return out


def set_theme(page: Page, theme: str) -> None:
    """Switch the NiceGUI app to the given theme ('light' or 'dark')."""
    icon = "light_mode" if theme == "light" else "dark_mode"
    btn = page.locator(f'button:has(i:text("{icon}"))')
    if btn.is_visible(timeout=3000):
        btn.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        # Move mouse away from the button to clear hover state
        page.mouse.move(640, 450)
        page.wait_for_timeout(200)


def enable_demo_mode(page: Page) -> None:
    """Activate demo mode (CSS blur on all media) for privacy."""
    page.evaluate("document.body.classList.add('demo-mode')")


# -- helpers ------------------------------------------------------------------


def _convert_server_coverage() -> None:
    """Convert server .coverage data to XML and merge with pytest's coverage."""
    if not _SERVER_COVERAGE_FILE.exists():
        return
    subprocess.run(
        ["uv", "run", "coverage", "xml", "-o", str(_REPO_ROOT / "tests" / "e2e-coverage.xml")],
        env={**os.environ, "COVERAGE_FILE": str(_SERVER_COVERAGE_FILE)},
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    _SERVER_COVERAGE_FILE.unlink(missing_ok=True)


def _server_is_ready(base_url: str = _BASE_URL) -> bool:
    try:
        r = httpx.get(base_url, timeout=2.0, follow_redirects=True)
        return r.status_code < 500
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _process_log_tail(proc: subprocess.Popen, log_path: Path | None) -> str:  # type: ignore[type-arg]
    """Return bounded startup diagnostics from a log file or captured pipes."""
    if log_path is not None and log_path.exists():
        return log_path.read_text(errors="replace")[-4000:]
    stdout = (proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
    stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
    return f"{stdout}\n{stderr}"[-4000:]


def _wait_for_server(
    proc: subprocess.Popen,  # type: ignore[type-arg]
    *,
    base_url: str = _BASE_URL,
    log_path: Path | None = None,
) -> None:
    """Wait for one E2E app process or fail with its bounded log tail."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(
                f"UI server exited early with code {proc.returncode}:\n"
                f"{_process_log_tail(proc, log_path)}"
            )
        if _server_is_ready(base_url):
            return
        time.sleep(0.5)
    proc.terminate()
    proc.wait(timeout=5)
    pytest.fail(
        f"UI server did not start within {_STARTUP_TIMEOUT}s:\n{_process_log_tail(proc, log_path)}"
    )
