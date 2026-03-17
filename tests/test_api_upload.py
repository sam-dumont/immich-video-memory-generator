"""Tests for Immich upload and album API methods."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.api.immich import ImmichClient, SyncImmichClient


@pytest.fixture()
def _mock_config():
    cfg = MagicMock()
    cfg.immich.url = "https://immich.example.com"
    cfg.immich.api_key = "test-api-key"
    with patch("immich_memories.config.get_config", return_value=cfg):
        yield cfg


def _json_response(data: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "/test"),
        json=data,
        headers={"content-type": "application/json"},
    )


class TestUploadAsset:
    @pytest.mark.asyncio
    async def test_upload_asset_sends_multipart(self, _mock_config, tmp_path):
        """upload_asset sends the file as multipart form data."""
        video_file = tmp_path / "memory.mp4"
        video_file.write_bytes(b"fake video content")

        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "asset-123", "status": "created"})
        )

        result = await client.upload_asset(video_file)

        assert result == "asset-123"
        call_kwargs = client._client.request.call_args
        assert call_kwargs[1].get("files") or call_kwargs[1].get("content")

    @pytest.mark.asyncio
    async def test_upload_asset_file_not_found(self, _mock_config):
        """upload_asset raises FileNotFoundError for missing file."""
        client = ImmichClient()
        with pytest.raises(FileNotFoundError):
            await client.upload_asset(Path("/nonexistent/video.mp4"))


class TestCreateAlbum:
    @pytest.mark.asyncio
    async def test_create_album_returns_id(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "album-456", "albumName": "2024 Memories"})
        )

        album_id = await client.create_album("2024 Memories", "Best of 2024")

        assert album_id == "album-456"
        call_kwargs = client._client.request.call_args[1]
        assert call_kwargs["json"]["albumName"] == "2024 Memories"
        assert call_kwargs["json"]["description"] == "Best of 2024"

    @pytest.mark.asyncio
    async def test_create_album_no_description(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=_json_response({"id": "album-789"}))

        album_id = await client.create_album("Summer 2024")
        assert album_id == "album-789"


class TestAddAssetsToAlbum:
    @pytest.mark.asyncio
    async def test_add_assets_sends_ids(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response([{"id": "asset-1", "success": True}])
        )

        await client.add_assets_to_album("album-456", ["asset-1", "asset-2"])

        call_args = client._client.request.call_args
        assert call_args[1]["json"]["ids"] == ["asset-1", "asset-2"]


class TestGetAlbums:
    @pytest.mark.asyncio
    async def test_get_albums(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response(
                [
                    {"id": "a1", "albumName": "Summer 2024"},
                    {"id": "a2", "albumName": "Winter 2024"},
                ]
            )
        )

        albums = await client.get_albums()
        assert len(albums) == 2
        assert albums[0]["id"] == "a1"

    @pytest.mark.asyncio
    async def test_find_album_by_name_found(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response(
                [
                    {"id": "a1", "albumName": "Summer 2024"},
                    {"id": "a2", "albumName": "2024 Memories"},
                ]
            )
        )

        album_id = await client.find_album_by_name("2024 Memories")
        assert album_id == "a2"

    @pytest.mark.asyncio
    async def test_find_album_by_name_not_found(self, _mock_config):
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response(
                [
                    {"id": "a1", "albumName": "Summer 2024"},
                ]
            )
        )

        album_id = await client.find_album_by_name("Nonexistent")
        assert album_id is None


class TestUploadMemory:
    @pytest.mark.asyncio
    async def test_upload_and_create_album(self, _mock_config, tmp_path):
        """Uploads video, creates album, adds asset to it."""
        video = tmp_path / "memory.mp4"
        video.write_bytes(b"video data")

        client = ImmichClient()
        # WHY: mock at service level — upload_memory lives on AlbumService
        client.albums.upload_asset = AsyncMock(return_value="asset-999")
        client.albums.find_album_by_name = AsyncMock(return_value=None)
        client.albums.create_album = AsyncMock(return_value="album-new")
        client.albums.add_assets_to_album = AsyncMock()

        result = await client.upload_memory(video, album_name="2024 Memories")

        assert result["asset_id"] == "asset-999"
        assert result["album_id"] == "album-new"
        client.albums.upload_asset.assert_awaited_once_with(video)
        client.albums.create_album.assert_awaited_once_with("2024 Memories")
        client.albums.add_assets_to_album.assert_awaited_once_with("album-new", ["asset-999"])

    @pytest.mark.asyncio
    async def test_upload_to_existing_album(self, _mock_config, tmp_path):
        """Reuses existing album if name matches."""
        video = tmp_path / "memory.mp4"
        video.write_bytes(b"video data")

        client = ImmichClient()
        # WHY: mock at service level — upload_memory lives on AlbumService
        client.albums.upload_asset = AsyncMock(return_value="asset-999")
        client.albums.find_album_by_name = AsyncMock(return_value="album-existing")
        client.albums.create_album = AsyncMock()
        client.albums.add_assets_to_album = AsyncMock()

        result = await client.upload_memory(video, album_name="2024 Memories")

        assert result["album_id"] == "album-existing"
        client.albums.create_album.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_without_album(self, _mock_config, tmp_path):
        """Upload only, no album creation."""
        video = tmp_path / "memory.mp4"
        video.write_bytes(b"video data")

        client = ImmichClient()
        # WHY: mock at service level — upload_memory lives on AlbumService
        client.albums.upload_asset = AsyncMock(return_value="asset-999")

        result = await client.upload_memory(video)

        assert result["asset_id"] == "asset-999"
        assert result["album_id"] is None


class TestSyncUploadWrappers:
    def test_sync_upload_memory(self, _mock_config, tmp_path):
        video = tmp_path / "memory.mp4"
        video.write_bytes(b"video data")

        client = SyncImmichClient()
        # WHY: mock at service level — upload_memory delegates to albums service
        client._async_client.albums.upload_asset = AsyncMock(return_value="asset-123")
        client._async_client.albums.find_album_by_name = AsyncMock(return_value=None)
        client._async_client.albums.create_album = AsyncMock(return_value="album-1")
        client._async_client.albums.add_assets_to_album = AsyncMock()

        result = client.upload_memory(video, album_name="Test Album")

        assert result["asset_id"] == "asset-123"
        assert result["album_id"] == "album-1"


class TestUploadConfig:
    def test_default_config(self):
        from immich_memories.config_models import UploadConfig

        cfg = UploadConfig()
        assert not cfg.enabled
        assert cfg.album_name is None

    def test_config_in_main_config(self):
        from immich_memories.config_loader import Config

        cfg = Config()
        assert not cfg.upload.enabled


class TestCLIUploadFlags:
    def test_generate_has_upload_flag(self):
        """CLI generate command accepts --upload-to-immich."""
        from click.testing import CliRunner

        from immich_memories.cli import main

        result = CliRunner().invoke(main, ["generate", "--help"])
        assert "--upload-to-immich" in result.output
        assert "--album" in result.output
