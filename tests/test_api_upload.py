"""Tests for Immich upload and album API methods."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.api.album_service import InvalidUploadResponse, build_upload_fields
from immich_memories.api.compatibility import ResolvedApiVersion, UnsupportedImmichVersion
from immich_memories.api.immich import ImmichClient, SyncImmichClient

_TEST_URL = "https://immich.example.com"
_TEST_KEY = "test-api-key"


@pytest.fixture()
def _mock_config():
    cfg = MagicMock()
    cfg.immich.url = _TEST_URL
    cfg.immich.api_key = _TEST_KEY
    with patch("immich_memories.config.get_config", return_value=cfg):
        yield cfg


def _json_response(data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "/test"),
        json=data,
        headers={"content-type": "application/json"},
    )


@pytest.fixture()
def video_path(tmp_path: Path) -> Path:
    path = tmp_path / "memory clip.mp4"
    path.write_bytes(b"fake video content")
    os.utime(path, (1_704_164_645, 1_704_164_645))
    return path


class TestUploadAsset:
    def test_v3_upload_fields_match_the_v3_contract(self, tmp_path: Path) -> None:
        video_file = tmp_path / "holiday memory.mp4"
        modified_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

        fields = build_upload_fields(ResolvedApiVersion.V3, video_file, modified_at)

        assert fields == {
            "filename": "holiday memory.mp4",
            "fileCreatedAt": "2024-01-02T03:04:05+00:00",
            "fileModifiedAt": "2024-01-02T03:04:05+00:00",
        }

    def test_v2_upload_fields_keep_deterministic_device_identity(self, tmp_path: Path) -> None:
        video_file = tmp_path / "holiday memory.mp4"
        video_file.write_bytes(b"fake video content")
        modified_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

        fields = build_upload_fields(ResolvedApiVersion.V2, video_file, modified_at)

        assert fields == {
            "deviceAssetId": "immich-memories-8b374193fa92cfcd",
            "deviceId": "immich-memories",
            "fileCreatedAt": "2024-01-02T03:04:05+00:00",
            "fileModifiedAt": "2024-01-02T03:04:05+00:00",
        }

    @pytest.mark.asyncio
    async def test_v3_upload_sends_exact_multipart_contract(self, video_path: Path) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v3")
        # WHY: replace the external Immich write while preserving the real upload service.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "asset-v3", "status": "created"})
        )

        result = await client.upload_asset(video_path)

        assert result == "asset-v3"
        assert client._client.request.await_count == 1
        request = client._client.request.await_args
        assert request.args == ("POST", "/api/assets")
        assert request.kwargs["data"] == {
            "filename": "memory clip.mp4",
            "fileCreatedAt": "2024-01-02T03:04:05+00:00",
            "fileModifiedAt": "2024-01-02T03:04:05+00:00",
        }
        assert "assetData" not in request.kwargs["data"]
        assert set(request.kwargs["files"]) == {"assetData"}
        asset_data = request.kwargs["files"]["assetData"]
        assert asset_data[0] == "memory clip.mp4"
        assert asset_data[2] == "video/mp4"
        assert request.kwargs["timeout"] == 600.0

    @pytest.mark.asyncio
    async def test_v3_mov_upload_uses_quicktime_mime_and_preserves_bytes(
        self, tmp_path: Path
    ) -> None:
        video_path = tmp_path / "holiday memory.mov"
        video_bytes = b"prores-in-quicktime"
        video_path.write_bytes(video_bytes)
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v3")
        captured_bytes: bytes | None = None

        async def capture_upload(*_args, **kwargs):
            nonlocal captured_bytes
            captured_bytes = kwargs["files"]["assetData"][1].read()
            return _json_response({"id": "asset-mov", "status": "created"})

        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(side_effect=capture_upload)

        result = await client.upload_asset(video_path)

        assert result == "asset-mov"
        request = client._client.request.await_args
        assert request.kwargs["data"]["filename"] == "holiday memory.mov"
        assert request.kwargs["files"]["assetData"][:1] == ("holiday memory.mov",)
        assert request.kwargs["files"]["assetData"][2] == "video/quicktime"
        assert captured_bytes == video_bytes

    @pytest.mark.asyncio
    async def test_mov_upload_suffix_is_case_insensitive(self, tmp_path: Path) -> None:
        video_path = tmp_path / "holiday.MOV"
        video_path.write_bytes(b"prores-in-quicktime")
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v2")
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "asset-mov", "status": "created"})
        )

        await client.upload_asset(video_path)

        request = client._client.request.await_args
        assert request.kwargs["files"]["assetData"][2] == "video/quicktime"

    @pytest.mark.asyncio
    async def test_upload_rejects_unsupported_suffix_before_transport(self, tmp_path: Path) -> None:
        video_path = tmp_path / "holiday.mkv"
        video_path.write_bytes(b"matroska")
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v3")
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "asset-mkv", "status": "created"})
        )

        with pytest.raises(ValueError, match=r"\.mkv"):
            await client.upload_asset(video_path)

        client._client.request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_v2_upload_sends_exact_multipart_contract(self, video_path: Path) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v2")
        # WHY: replace the external Immich write while preserving the real upload service.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "asset-v2", "status": "created"})
        )

        result = await client.upload_asset(video_path)

        assert result == "asset-v2"
        assert client._client.request.await_count == 1
        request = client._client.request.await_args
        assert request.args == ("POST", "/api/assets")
        assert request.kwargs["data"] == {
            "deviceAssetId": "immich-memories-e1a1f100d5f573b1",
            "deviceId": "immich-memories",
            "fileCreatedAt": "2024-01-02T03:04:05+00:00",
            "fileModifiedAt": "2024-01-02T03:04:05+00:00",
        }
        assert "assetData" not in request.kwargs["data"]
        assert set(request.kwargs["files"]) == {"assetData"}
        asset_data = request.kwargs["files"]["assetData"]
        assert asset_data[0] == "memory clip.mp4"
        assert asset_data[2] == "video/mp4"
        assert request.kwargs["timeout"] == 600.0

    @pytest.mark.asyncio
    async def test_auto_upload_resolves_once_before_posting(self, video_path: Path) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        # WHY: provide controlled Immich version and upload responses without network I/O.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            side_effect=[
                _json_response({"major": 3, "minor": 1, "patch": 0}),
                _json_response({"id": "asset-one", "status": "created"}),
                _json_response({"id": "asset-two", "status": "created"}),
            ]
        )

        assert await client.upload_asset(video_path) == "asset-one"
        assert await client.upload_asset(video_path) == "asset-two"

        requests = client._client.request.await_args_list
        assert [request.args[:2] for request in requests] == [
            ("GET", "/api/server/version"),
            ("POST", "/api/assets"),
            ("POST", "/api/assets"),
        ]
        assert requests[1].kwargs["data"] == {
            "filename": "memory clip.mp4",
            "fileCreatedAt": "2024-01-02T03:04:05+00:00",
            "fileModifiedAt": "2024-01-02T03:04:05+00:00",
        }

    @pytest.mark.asyncio
    async def test_unsupported_auto_version_does_not_open_or_upload_file(
        self, video_path: Path
    ) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        # WHY: return an unsupported real-world server version without network I/O.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"major": 4, "minor": 0, "patch": 0})
        )

        # WHY: file opening is the boundary that proves no upload bytes are read.
        with (
            patch.object(Path, "open") as open_file,
            pytest.raises(UnsupportedImmichVersion, match="major version 4"),
        ):
            await client.upload_asset(video_path)

        open_file.assert_not_called()
        requests = client._client.request.await_args_list
        assert [request.args[:2] for request in requests] == [("GET", "/api/server/version")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response_body",
        [
            pytest.param({"status": "created"}, id="missing-id"),
            pytest.param({"id": ""}, id="empty-id"),
            pytest.param({"id": 123}, id="non-string-id"),
            pytest.param([], id="non-object-body"),
        ],
    )
    async def test_malformed_upload_response_raises_typed_error(
        self, video_path: Path, response_body: object
    ) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v3")
        # WHY: return a malformed successful Immich response without a network write.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=_json_response(response_body))

        with pytest.raises(InvalidUploadResponse, match="non-empty string id"):
            await client.upload_asset(video_path)

    @pytest.mark.asyncio
    async def test_duplicate_upload_response_returns_existing_asset_id(
        self, video_path: Path
    ) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v2")
        # WHY: represent Immich's duplicate response without performing a network write.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            return_value=_json_response({"id": "existing-asset", "status": "duplicate"})
        )

        assert await client.upload_asset(video_path) == "existing-asset"

    @pytest.mark.asyncio
    async def test_upload_asset_sends_multipart(self, _mock_config, tmp_path):
        """upload_asset sends the file as multipart form data."""
        video_file = tmp_path / "memory.mp4"
        video_file.write_bytes(b"fake video content")

        client = ImmichClient(_TEST_URL, _TEST_KEY, api_version="v2")
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        with pytest.raises(FileNotFoundError):
            await client.upload_asset(Path("/nonexistent/video.mp4"))


class TestCreateAlbum:
    @pytest.mark.asyncio
    async def test_create_album_returns_id(self, _mock_config):
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=_json_response({"id": "album-789"}))

        album_id = await client.create_album("Summer 2024")
        assert album_id == "album-789"


class TestAddAssetsToAlbum:
    @pytest.mark.asyncio
    async def test_add_assets_sends_ids(self, _mock_config):
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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

        client = ImmichClient(_TEST_URL, _TEST_KEY)
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

        client = ImmichClient(_TEST_URL, _TEST_KEY)
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

        client = ImmichClient(_TEST_URL, _TEST_KEY)
        # WHY: mock at service level — upload_memory lives on AlbumService
        client.albums.upload_asset = AsyncMock(return_value="asset-999")

        result = await client.upload_memory(video)

        assert result["asset_id"] == "asset-999"
        assert result["album_id"] is None


class TestSyncUploadWrappers:
    def test_sync_upload_memory(self, _mock_config, tmp_path):
        video = tmp_path / "memory.mp4"
        video.write_bytes(b"video data")

        client = SyncImmichClient(_TEST_URL, _TEST_KEY)
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
