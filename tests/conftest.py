"""Shared test fixtures and factories for immich-video-memory-generator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from immich_memories.api.models import Asset, AssetType, ExifInfo, VideoClipInfo
from immich_memories.config_loader import Config


def make_asset(
    asset_id: str = "test-asset-001",
    *,
    is_favorite: bool = False,
    file_created_at: datetime | None = None,
    original_file_name: str = "VID_001.MOV",
    exif_make: str | None = "Apple",
    exif_model: str | None = "iPhone 15 Pro",
    duration: str | None = "0:00:10.000",
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
