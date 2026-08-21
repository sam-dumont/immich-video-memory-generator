"""Shared test fixtures and factories for immich-video-memory-generator."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories import config_loader
from immich_memories.api.models import Asset, AssetType, ExifInfo, VideoClipInfo
from immich_memories.config_loader import Config

_TEST_ROOT: Path | None = None
_TEST_ENV_KEYS = {
    "IMMICH_MEMORIES_CACHE__DATABASE": "cache.db",
    "IMMICH_MEMORIES_CACHE__DIRECTORY": "cache",
    "IMMICH_MEMORIES_OUTPUT__DIRECTORY": "output",
}
_ORIGINAL_TEST_ENV: dict[str, str | None] = {}


def pytest_configure(config: pytest.Config) -> None:
    """Route test configuration paths to one disposable session directory."""
    del config
    global _TEST_ROOT
    _TEST_ROOT = Path(tempfile.mkdtemp(prefix="immich-memories-pytest-"))

    for key, relative in _TEST_ENV_KEYS.items():
        _ORIGINAL_TEST_ENV[key] = os.environ.get(key)
        os.environ[key] = str(_TEST_ROOT / relative)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore the process environment and remove the validated test root."""
    del config
    global _TEST_ROOT

    for key, original_value in _ORIGINAL_TEST_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    _ORIGINAL_TEST_ENV.clear()

    if _TEST_ROOT is None:
        return

    test_root = _TEST_ROOT
    _TEST_ROOT = None
    temp_root = Path(tempfile.gettempdir()).resolve()
    resolved_test_root = test_root.resolve()
    if not (
        test_root.name.startswith("immich-memories-pytest-")
        and resolved_test_root.is_relative_to(temp_root)
    ):
        raise RuntimeError(f"Refusing to remove unvalidated pytest root: {test_root}")

    shutil.rmtree(test_root)


@pytest.fixture(autouse=True)
def isolated_user_paths() -> Iterator[Path]:
    """Reset cached settings and assert tests never resolve user directories."""
    assert _TEST_ROOT is not None
    config_loader._config = None
    yield _TEST_ROOT
    config_loader._config = None

    config = Config()
    normal_user_paths = {
        Path.home() / ".immich-memories" / "cache.db",
        Path.home() / ".immich-memories" / "cache",
        Path.home() / "Videos" / "Memories",
    }
    resolved_paths = {
        config.cache.database_path,
        config.cache.cache_path,
        config.output.output_path,
    }
    assert not resolved_paths & normal_user_paths


def make_asset(
    asset_id: str = "test-asset-001",
    *,
    is_favorite: bool = False,
    file_created_at: datetime | None = None,
    original_file_name: str = "VID_001.MOV",
    exif_make: str | None = "Apple",
    exif_model: str | None = "iPhone 15 Pro",
    duration: str | int | float | None = "0:00:10.000",
) -> Asset:
    """Create an Asset with sensible defaults for testing."""
    now = file_created_at or datetime.now(tz=UTC)
    exif = ExifInfo(make=exif_make, model=exif_model) if exif_make or exif_model else None
    return Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=now,
        fileModifiedAt=now,
        updatedAt=now,
        isFavorite=is_favorite,
        originalFileName=original_file_name,
        exifInfo=exif,
        duration=duration,
    )


@pytest.fixture()
def asset_payload() -> dict[str, object]:
    """Provide a minimal raw Immich asset response payload."""
    now = datetime.now(tz=UTC)
    return {
        "id": "asset-payload-001",
        "type": "VIDEO",
        "fileCreatedAt": now,
        "fileModifiedAt": now,
        "updatedAt": now,
    }


def make_clip(
    asset_id: str = "test-clip-001",
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float = 5.0,
    bitrate: int = 10_000_000,
    codec: str = "hevc",
    is_favorite: bool = False,
    color_transfer: str | None = None,
    exif_make: str | None = "Apple",
    exif_model: str | None = "iPhone 15 Pro",
    file_created_at: datetime | None = None,
) -> VideoClipInfo:
    """Create a VideoClipInfo with sensible defaults for testing."""
    asset = make_asset(
        asset_id,
        is_favorite=is_favorite,
        exif_make=exif_make,
        exif_model=exif_model,
        file_created_at=file_created_at,
        duration=f"0:00:{duration:06.3f}",
    )
    return VideoClipInfo(
        asset=asset,
        width=width,
        height=height,
        duration_seconds=duration,
        bitrate=bitrate,
        codec=codec,
        color_transfer=color_transfer,
    )


@pytest.fixture()
def mock_immich_client() -> MagicMock:
    """Mock SyncImmichClient with empty defaults."""
    client = MagicMock()
    client.get_videos_for_date_range.return_value = []
    client.get_all_videos_for_year.return_value = []
    client.get_asset_thumbnail.return_value = b"\x00" * 100
    return client


@pytest.fixture()
def mock_analysis_cache(tmp_path: Path) -> MagicMock:
    """Mock VideoAnalysisCache with empty defaults."""
    cache = MagicMock()
    cache.get_cached_analysis.return_value = None
    cache.db_path = tmp_path / "test_cache.db"
    return cache


@pytest.fixture()
def mock_thumbnail_cache(tmp_path: Path) -> MagicMock:
    """Mock ThumbnailCache with empty defaults."""
    cache = MagicMock()
    cache.get.return_value = None
    cache.cache_dir = tmp_path / "thumbnails"
    return cache


@pytest.fixture()
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary directory for test output files."""
    output = tmp_path / "output"
    output.mkdir()
    return output


@pytest.fixture()
def sample_config() -> Config:
    """Provide a Config with safe defaults for testing."""
    return Config()


@pytest.fixture(autouse=True)
def _no_leaked_drain_threads():
    """Fail any test that leaves a stderr drain thread spinning.

    A mocked pipe whose read() never returns b"" (constant return_value, or a
    bare MagicMock — its __iter__ yields nothing and __bool__ is True) traps
    drain_stderr_tail in an infinite loop. The thread outlives the test at
    ~250k read()/s, MagicMock records every call, and the suite bloats by
    gigabytes until the 7 GB macOS CI runner kills pytest (exit 137).
    """
    yield
    leaked = [t for t in threading.enumerate() if "drain_stderr_tail" in t.name and t.is_alive()]
    assert not leaked, (
        f"stderr drain thread(s) outlived this test: {leaked} — a mocked pipe "
        'never reached EOF; use stderr.read.side_effect = [data, b""]'
    )


def pytest_collection_modifyitems(items) -> None:
    """Mark everything under tests/integration/ as integration, by path.

    WHY: `pytestmark` in a conftest does nothing, so a forgotten decorator
    silently runs an integration test in every unit job (#450). Path-based
    marking cannot be forgotten.
    """
    for item in items:
        if "tests/integration" in item.path.as_posix():
            item.add_marker("integration")
