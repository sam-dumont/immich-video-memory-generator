"""Tests for Immich API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.api.immich import (
    ImmichAPIError,
    ImmichAuthError,
    ImmichClient,
    ImmichNotFoundError,
    SyncImmichClient,
)
from immich_memories.api.models import MetadataSearchResult


@pytest.fixture()
def _mock_config():
    """Patch get_config so ImmichClient can be constructed without real config."""
    cfg = MagicMock()
    cfg.immich.url = "https://immich.example.com"
    cfg.immich.api_key = "test-api-key"
    with patch("immich_memories.config.get_config", return_value=cfg):
        yield cfg


class TestImmichClientInit:
    """Initialization and validation."""

    def test_missing_url_raises(self):
        """Empty URL raises ValueError."""
        cfg = MagicMock()
        cfg.immich.url = ""
        cfg.immich.api_key = "key"
        with pytest.raises(ValueError, match="URL not configured"):
            ImmichClient(config=cfg)

    def test_missing_api_key_raises(self):
        """Empty API key raises ValueError."""
        cfg = MagicMock()
        cfg.immich.url = "https://x.com"
        cfg.immich.api_key = ""
        with pytest.raises(ValueError, match="API key not configured"):
            ImmichClient(config=cfg)

    def test_strips_trailing_slash(self, _mock_config):
        """Trailing slash is removed from base_url."""
        client = ImmichClient(base_url="https://x.com/", api_key="key")
        assert client.base_url == "https://x.com"

    def test_config_fallback(self, _mock_config):
        """Falls back to config when no explicit args."""
        client = ImmichClient()
        assert client.base_url == "https://immich.example.com"
        assert client.api_key == "test-api-key"

    def test_explicit_args_override_config(self, _mock_config):
        """Explicit args take precedence over config."""
        client = ImmichClient(base_url="https://other.com", api_key="other-key")
        assert client.base_url == "https://other.com"
        assert client.api_key == "other-key"


class TestImmichClientRequest:
    """HTTP request handling and error mapping."""

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, _mock_config):
        """401 status maps to ImmichAuthError."""
        client = ImmichClient()
        mock_response = httpx.Response(401, request=httpx.Request("GET", "/test"))
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(ImmichAuthError, match="Invalid API key"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self, _mock_config):
        """404 status maps to ImmichNotFoundError."""
        client = ImmichClient()
        mock_response = httpx.Response(404, request=httpx.Request("GET", "/test"))
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(ImmichNotFoundError, match="not found"):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_500_raises_api_error(self, _mock_config):
        """5xx status maps to ImmichAPIError with status code."""
        client = ImmichClient()
        mock_response = httpx.Response(
            500,
            request=httpx.Request("GET", "/test"),
            content=b"Internal Server Error",
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        # WHY: avoid real sleep — retry backoff would add seconds
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ImmichAPIError) as exc_info,
        ):
            await client._request("GET", "/test")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout_raises_api_error(self, _mock_config):
        """Timeout wraps as ImmichAPIError."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        # WHY: avoid real sleep — retry backoff would add seconds
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ImmichAPIError, match="timed out"),
        ):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_request_error_raises_api_error(self, _mock_config):
        """Connection error wraps as ImmichAPIError."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        # WHY: avoid real sleep — retry backoff would add seconds
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ImmichAPIError, match="Request failed"),
        ):
            await client._request("GET", "/test")

    @pytest.mark.asyncio
    async def test_json_response_parsed(self, _mock_config):
        """JSON content-type returns parsed dict."""
        client = ImmichClient()
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/test"),
            json={"key": "value"},
            headers={"content-type": "application/json"},
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client._request("GET", "/test")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_binary_response_returned(self, _mock_config):
        """Non-JSON content-type returns bytes."""
        client = ImmichClient()
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/test"),
            content=b"\x89PNG",
            headers={"content-type": "image/png"},
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        result = await client._request("GET", "/test")
        assert result == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_error_json_body_extracted(self, _mock_config):
        """Error responses with JSON body extract 'message' field."""
        client = ImmichClient()
        mock_response = httpx.Response(
            400,
            request=httpx.Request("POST", "/test"),
            json={"message": "Bad request: missing field"},
            headers={"content-type": "application/json"},
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(ImmichAPIError, match="Bad request: missing field"):
            await client._request("POST", "/test")


class TestImmichClientLifecycle:
    """Context manager and connection lifecycle."""

    @pytest.mark.asyncio
    async def test_context_manager_closes(self, _mock_config):
        """Async context manager calls close on exit."""
        async with ImmichClient() as client:
            assert client.base_url == "https://immich.example.com"
        assert client._client is None

    @pytest.mark.asyncio
    async def test_client_property_creates_lazily(self, _mock_config):
        """client property lazily creates httpx.AsyncClient."""
        client = ImmichClient()
        assert client._client is None
        http_client = client.client
        assert isinstance(http_client, httpx.AsyncClient)
        await client.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, _mock_config):
        """Calling close() twice is safe."""
        client = ImmichClient()
        _ = client.client  # Force creation
        await client.close()
        await client.close()  # Should not raise


class TestSyncImmichClient:
    """Synchronous wrapper tests."""

    def test_sync_context_manager(self, _mock_config):
        """Sync context manager works."""
        with SyncImmichClient() as client:
            assert client.base_url == "https://immich.example.com"

    def test_sync_properties(self, _mock_config):
        """Properties delegate to async client."""
        client = SyncImmichClient()
        assert client.base_url == "https://immich.example.com"
        assert client.api_key == "test-api-key"
        assert client.timeout == 30.0
        client.close()


class TestSearchPagination:
    """Tests for search/pagination methods."""

    @pytest.mark.asyncio
    async def test_single_page_result(self, _mock_config):
        """Single page result returns all assets."""
        client = ImmichClient()
        mock_result = MetadataSearchResult(
            assets={"total": 1, "items": [], "nextPage": None},
        )
        # WHY: mock at service level — get_all_videos_for_year delegates to search service
        client.search.search_metadata = AsyncMock(return_value=mock_result)

        result = await client.get_all_videos_for_year(2024)
        assert not result
        client.search.search_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page_accumulation(self, _mock_config):
        """Multi-page results accumulate all assets."""
        from tests.conftest import make_asset

        client = ImmichClient()
        a1 = make_asset("a1")
        a2 = make_asset("a2")

        page1 = MetadataSearchResult(
            assets={"total": 2, "items": [a1.model_dump(by_alias=True)], "nextPage": "2"},
        )
        page2 = MetadataSearchResult(
            assets={"total": 2, "items": [a2.model_dump(by_alias=True)], "nextPage": None},
        )
        # WHY: mock at service level — get_all_videos_for_year delegates to search service
        client.search.search_metadata = AsyncMock(side_effect=[page1, page2])

        result = await client.get_all_videos_for_year(2024)
        assert len(result) == 2
        assert client.search.search_metadata.call_count == 2

    @pytest.mark.asyncio
    async def test_progress_callback_invoked(self, _mock_config):
        """Progress callback receives accumulated count."""
        client = ImmichClient()
        mock_result = MetadataSearchResult(
            assets={"total": 0, "items": [], "nextPage": None},
        )
        # WHY: mock at service level — delegates to search service
        client.search.search_metadata = AsyncMock(return_value=mock_result)

        callback = MagicMock()
        await client.get_all_videos_for_year(2024, progress_callback=callback)
        callback.assert_called_once_with(0, None)

    @pytest.mark.asyncio
    async def test_empty_year_returns_empty(self, _mock_config):
        """Year with no videos returns empty list."""
        client = ImmichClient()
        mock_result = MetadataSearchResult(
            assets={"total": 0, "items": [], "nextPage": None},
        )
        # WHY: mock at service level — delegates to search service
        client.search.search_metadata = AsyncMock(return_value=mock_result)

        result = await client.get_all_videos_for_year(1999)
        assert not result


class TestPersonMethods:
    """Tests for person-related API methods."""

    @pytest.mark.asyncio
    async def test_get_all_people_dict_format(self, _mock_config):
        """Handles {'people': [...]} response format."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/people"),
            json={"people": [{"id": "p1", "name": "Alice"}]},
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        people = await client.get_all_people()
        assert len(people) == 1
        assert people[0].name == "Alice"

    @pytest.mark.asyncio
    async def test_get_all_people_list_format(self, _mock_config):
        """Handles direct list response format."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/people"),
            json=[{"id": "p1", "name": "Bob"}],
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        people = await client.get_all_people()
        assert len(people) == 1
        assert people[0].name == "Bob"

    @pytest.mark.asyncio
    async def test_get_person_by_name_case_insensitive(self, _mock_config):
        """Name search is case-insensitive."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/people"),
            json={"people": [{"id": "p1", "name": "Alice"}]},
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        person = await client.get_person_by_name("ALICE")
        assert person is not None
        assert person.name == "Alice"

    @pytest.mark.asyncio
    async def test_get_person_by_name_not_found(self, _mock_config):
        """Returns None when name not found."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/people"),
            json={"people": []},
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        person = await client.get_person_by_name("Nobody")
        assert person is None


class TestAvailableYears:
    """Tests for get_available_years."""

    @pytest.mark.asyncio
    async def test_parses_years_from_buckets(self, _mock_config):
        """Extracts years from time bucket ISO strings."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/timeline/buckets"),
            json=[
                {"timeBucket": "2024-01-01T00:00:00.000Z", "count": 5},
                {"timeBucket": "2023-06-01T00:00:00.000Z", "count": 3},
                {"timeBucket": "2024-07-01T00:00:00.000Z", "count": 2},
            ],
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        years = await client.get_available_years()
        assert years == [2024, 2023]  # Descending, deduplicated

    @pytest.mark.asyncio
    async def test_invalid_bucket_skipped(self, _mock_config):
        """Invalid ISO strings are silently skipped."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/timeline/buckets"),
            json=[
                {"timeBucket": "not-a-date", "count": 1},
                {"timeBucket": "2023-01-01T00:00:00.000Z", "count": 1},
            ],
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        years = await client.get_available_years()
        assert years == [2023]

    @pytest.mark.asyncio
    async def test_empty_buckets(self, _mock_config):
        """No buckets returns empty list."""
        client = ImmichClient()
        client._client = AsyncMock()
        client._client.is_closed = False
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "/timeline/buckets"),
            json=[],
            headers={"content-type": "application/json"},
        )
        client._client.request = AsyncMock(return_value=mock_response)

        years = await client.get_available_years()
        assert not years


class TestGetVideosForAnyPerson:
    """Tests for OR-query across multiple person IDs."""

    @pytest.mark.asyncio
    async def test_single_person_returns_same_as_regular_query(self, _mock_config):
        """Single person_id delegates to get_videos_for_person_and_date_range."""
        from datetime import UTC, datetime

        from immich_memories.timeperiod import DateRange
        from tests.conftest import make_asset

        client = ImmichClient()
        a1 = make_asset("a1", file_created_at=datetime(2024, 3, 1, tzinfo=UTC))
        a2 = make_asset("a2", file_created_at=datetime(2024, 6, 1, tzinfo=UTC))
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))

        # WHY: mock at service level — get_videos_for_any_person delegates to search service
        client.search.get_videos_for_person_and_date_range = AsyncMock(return_value=[a1, a2])

        result = await client.get_videos_for_any_person(["person-1"], date_range)
        assert len(result) == 2
        assert result[0].id == "a1"
        assert result[1].id == "a2"

    @pytest.mark.asyncio
    async def test_two_people_deduplicates_shared_videos(self, _mock_config):
        """Videos appearing for both people are deduplicated in the union."""
        from datetime import UTC, datetime

        from immich_memories.timeperiod import DateRange
        from tests.conftest import make_asset

        client = ImmichClient()
        shared = make_asset("shared", file_created_at=datetime(2024, 2, 1, tzinfo=UTC))
        only_a = make_asset("only-a", file_created_at=datetime(2024, 1, 1, tzinfo=UTC))
        only_b = make_asset("only-b", file_created_at=datetime(2024, 3, 1, tzinfo=UTC))
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))

        async def mock_query(person_id, date_range, progress_callback=None):
            if person_id == "person-a":
                return [only_a, shared]
            return [shared, only_b]

        # WHY: mock at service level — delegates to search service
        client.search.get_videos_for_person_and_date_range = AsyncMock(side_effect=mock_query)

        result = await client.get_videos_for_any_person(["person-a", "person-b"], date_range)
        assert len(result) == 3
        ids = [a.id for a in result]
        assert ids == ["only-a", "shared", "only-b"]

    @pytest.mark.asyncio
    async def test_empty_person_list_returns_empty(self, _mock_config):
        """Empty person_ids list returns empty result without any queries."""
        from datetime import datetime

        from immich_memories.timeperiod import DateRange

        client = ImmichClient()
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))
        # WHY: mock at service level — delegates to search service
        client.search.get_videos_for_person_and_date_range = AsyncMock()

        result = await client.get_videos_for_any_person([], date_range)
        assert not result
        client.search.get_videos_for_person_and_date_range.assert_not_called()
