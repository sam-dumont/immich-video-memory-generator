"""Tests for Immich API client."""

from __future__ import annotations

import logging
import traceback
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from immich_memories.api.compatibility import ResolvedApiVersion
from immich_memories.api.immich import (
    ImmichAPIError,
    ImmichAuthError,
    ImmichClient,
    ImmichNotFoundError,
    SyncImmichClient,
    parse_error_response,
)
from immich_memories.api.models import MetadataSearchResult

_TEST_URL = "https://immich.example.com"
_TEST_KEY = "test-api-key"


@pytest.fixture()
def _mock_config():
    """Patch get_config so older test code that references config still works."""
    cfg = MagicMock()
    cfg.immich.url = _TEST_URL
    cfg.immich.api_key = _TEST_KEY
    with patch("immich_memories.config.get_config", return_value=cfg):
        yield cfg


class TestImmichClientInit:
    """Initialization and validation."""

    def test_missing_url_raises(self):
        """Empty URL raises ValueError."""
        with pytest.raises(ValueError, match="URL not configured"):
            ImmichClient(base_url="", api_key="key")

    def test_missing_api_key_raises(self):
        """Empty API key raises ValueError."""
        with pytest.raises(ValueError, match="API key not configured"):
            ImmichClient(base_url="https://x.com", api_key="")

    def test_strips_trailing_slash(self):
        """Trailing slash is removed from base_url."""
        client = ImmichClient(base_url="https://x.com/", api_key="key")
        assert client.base_url == "https://x.com"

    def test_explicit_args(self):
        """Explicit args set base_url and api_key."""
        client = ImmichClient(base_url="https://other.com", api_key="other-key")
        assert client.base_url == "https://other.com"
        assert client.api_key == "other-key"


class TestImmichClientRequest:
    """HTTP request handling and error mapping."""

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, _mock_config):
        """401 status maps to ImmichAuthError."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        mock_response = httpx.Response(
            401,
            request=httpx.Request("GET", "/test", headers={"x-api-key": _TEST_KEY}),
            json={"error": "Unauthorized", "message": "API key rejected"},
            headers={"X-Correlation-ID": "corr-auth"},
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(ImmichAuthError, match="Invalid API key") as raised:
            await client._request("GET", "/test")

        assert str(raised.value) == "Invalid API key"
        assert raised.value.status_code == 401
        assert raised.value.correlation_id == "corr-auth"
        assert raised.value.details == {
            "error": "Unauthorized",
            "message": "API key rejected",
        }
        assert _TEST_KEY not in str(raised.value)

    @pytest.mark.parametrize(
        ("response", "expected_message", "expected_body"),
        [
            pytest.param(
                httpx.Response(400, json={"message": "v2 validation failed"}),
                "v2 validation failed",
                {"message": "v2 validation failed"},
                id="v2-string-message",
            ),
            pytest.param(
                httpx.Response(
                    422,
                    json={"message": {"error": "nested validation failed"}},
                ),
                "nested validation failed",
                {"message": {"error": "nested validation failed"}},
                id="nested-object",
            ),
            pytest.param(
                httpx.Response(
                    400,
                    json={"message": ["first failure", {"message": "second failure"}]},
                ),
                "first failure; second failure",
                {"message": ["first failure", {"message": "second failure"}]},
                id="nested-list",
            ),
            pytest.param(
                httpx.Response(502, text="plain gateway failure"),
                "plain gateway failure",
                "plain gateway failure",
                id="plain-text",
            ),
        ],
    )
    def test_error_response_shapes_are_normalized(
        self,
        response: httpx.Response,
        expected_message: str,
        expected_body: object,
    ) -> None:
        details = parse_error_response(response)

        assert details.message == expected_message
        assert details.status_code == response.status_code
        assert details.body == expected_body

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self, _mock_config):
        """404 status maps to ImmichNotFoundError."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        mock_response = httpx.Response(
            404,
            request=httpx.Request("GET", "/test"),
            json={"error": "Not Found", "message": "Asset does not exist"},
            headers={"X-Correlation-ID": "corr-missing"},
        )
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=mock_response)

        with pytest.raises(ImmichNotFoundError, match="not found") as raised:
            await client._request("GET", "/test")

        assert raised.value.status_code == 404
        assert raised.value.correlation_id == "corr-missing"
        assert raised.value.details == {
            "error": "Not Found",
            "message": "Asset does not exist",
        }

    @pytest.mark.asyncio
    async def test_500_raises_api_error(self, _mock_config):
        """5xx status maps to ImmichAPIError with status code."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        mock_response = httpx.Response(
            500,
            request=httpx.Request("GET", "/test"),
            json={"message": "temporary outage", "retryable": True},
            headers={"X-Correlation-ID": "corr-retry"},
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
        assert str(exc_info.value) == "temporary outage"
        assert exc_info.value.status_code == 500
        assert exc_info.value.correlation_id == "corr-retry"
        assert exc_info.value.details == {
            "message": "temporary outage",
            "retryable": True,
        }

    @pytest.mark.asyncio
    async def test_timeout_raises_api_error(self, _mock_config):
        """Timeout wraps as ImmichAPIError."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
    async def test_timeout_retry_warnings_redact_configured_api_key(
        self, _mock_config, caplog: pytest.LogCaptureFixture
    ) -> None:
        api_key = "timeout-secret-91de"
        client = ImmichClient(_TEST_URL, api_key)
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            side_effect=httpx.TimeoutException(f"timed out with {api_key}")
        )

        # WHY: avoid real backoff while exercising the production retry/logger boundary.
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.WARNING, logger="immich_memories.api.immich"),
            pytest.raises(ImmichAPIError),
        ):
            await client._request("GET", "/test")

        warnings = "\n".join(record.getMessage() for record in caplog.records)
        assert api_key not in warnings
        assert warnings.count("Request failed: timed out with ***") == 2

    @pytest.mark.asyncio
    async def test_retryable_transport_error_redacts_final_exception_traceback(
        self, _mock_config
    ) -> None:
        api_key = "transport-secret-91de"
        client = ImmichClient(_TEST_URL, api_key)
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            side_effect=httpx.ConnectError(f"connection rejected {api_key}")
        )

        # WHY: avoid real backoff while retaining the exception and traceback users receive.
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ImmichAPIError) as raised,
        ):
            await client._request("GET", "/test")

        rendered_traceback = "".join(
            traceback.format_exception(raised.type, raised.value, raised.tb)
        )
        assert str(raised.value) == "Request failed: connection rejected ***"
        assert api_key not in rendered_traceback

    @pytest.mark.asyncio
    async def test_non_retryable_request_error_redacts_exception_traceback(
        self, _mock_config
    ) -> None:
        api_key = "request-secret-91de"
        client = ImmichClient(_TEST_URL, api_key)
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(
            side_effect=httpx.RequestError(f"request rejected {api_key}")
        )

        with pytest.raises(ImmichAPIError) as raised:
            await client._request("GET", "/test")

        rendered_traceback = "".join(
            traceback.format_exception(raised.type, raised.value, raised.tb)
        )
        assert str(raised.value) == "Request failed: request rejected ***"
        assert api_key not in rendered_traceback

    @pytest.mark.asyncio
    async def test_json_response_parsed(self, _mock_config):
        """JSON content-type returns parsed dict."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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

    @pytest.mark.asyncio
    async def test_v3_error_preserves_list_message_details_and_correlation_id(
        self, _mock_config
    ) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "/api/assets", headers={"x-api-key": _TEST_KEY}),
            json={"error": "Bad Request", "message": ["filename is required"]},
            headers={"X-Correlation-ID": "corr-123"},
        )
        # WHY: return a representative v3 API failure without network I/O.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=response)

        with pytest.raises(ImmichAPIError) as raised:
            await client._request("POST", "/assets")

        assert str(raised.value) == "filename is required"
        assert raised.value.status_code == 400
        assert raised.value.correlation_id == "corr-123"
        assert raised.value.details == {
            "error": "Bad Request",
            "message": ["filename is required"],
        }
        assert _TEST_KEY not in str(raised.value)

    @pytest.mark.asyncio
    async def test_error_message_redacts_api_key_from_response_body(self, _mock_config) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "/api/assets"),
            json={"message": f"x-api-key: {_TEST_KEY}"},
        )
        # WHY: reproduce a secret-bearing server message without network I/O.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=response)

        with pytest.raises(ImmichAPIError) as raised:
            await client._request("POST", "/assets")

        assert "***" in str(raised.value)
        assert _TEST_KEY not in str(raised.value)

    @pytest.mark.asyncio
    async def test_error_message_redacts_unlabeled_configured_api_key(self, _mock_config) -> None:
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "/api/assets"),
            json={"message": _TEST_KEY},
        )
        # WHY: reproduce a server echoing the configured credential without network I/O.
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(return_value=response)

        with pytest.raises(ImmichAPIError) as raised:
            await client._request("POST", "/assets")

        assert str(raised.value) == "***"
        assert _TEST_KEY not in str(raised.value)


class TestImmichClientLifecycle:
    """Context manager and connection lifecycle."""

    @pytest.mark.asyncio
    async def test_context_manager_closes(self, _mock_config):
        """Async context manager calls close on exit."""
        async with ImmichClient(_TEST_URL, _TEST_KEY) as client:
            assert client.base_url == "https://immich.example.com"
        assert client._client is None

    @pytest.mark.asyncio
    async def test_client_property_creates_lazily(self, _mock_config):
        """client property lazily creates httpx.AsyncClient."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        assert client._client is None
        http_client = client.client
        assert isinstance(http_client, httpx.AsyncClient)
        await client.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, _mock_config):
        """Calling close() twice is safe."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
        _ = client.client  # Force creation
        await client.close()
        await client.close()  # Should not raise


class TestSyncImmichClient:
    """Synchronous wrapper tests."""

    def test_sync_context_manager(self, _mock_config):
        """Sync context manager works."""
        with SyncImmichClient(_TEST_URL, _TEST_KEY) as client:
            assert client.base_url == "https://immich.example.com"

    def test_sync_properties(self, _mock_config):
        """Properties delegate to async client."""
        client = SyncImmichClient(_TEST_URL, _TEST_KEY)
        assert client.base_url == "https://immich.example.com"
        assert client.api_key == "test-api-key"
        assert client.timeout == 30.0
        client.close()

    def test_get_api_version_returns_resolved_version(self, _mock_config):
        client = SyncImmichClient(_TEST_URL, _TEST_KEY, api_version="v2")
        try:
            assert client.get_api_version() is ResolvedApiVersion.V2
        finally:
            client.close()


class TestSearchPagination:
    """Tests for search/pagination methods."""

    @pytest.mark.asyncio
    async def test_single_page_result(self, _mock_config):
        """Single page result returns all assets."""
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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

        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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
        client = ImmichClient(_TEST_URL, _TEST_KEY)
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


class TestGetVideosForAllPersons:
    """Naming several people asks for the videos holding all of them."""

    @pytest.mark.asyncio
    async def test_single_person_returns_that_person_s_videos(self, _mock_config):
        from datetime import UTC, datetime

        from immich_memories.timeperiod import DateRange
        from tests.conftest import make_asset

        client = ImmichClient(_TEST_URL, _TEST_KEY)
        a1 = make_asset("a1", file_created_at=datetime(2024, 3, 1, tzinfo=UTC))
        a2 = make_asset("a2", file_created_at=datetime(2024, 6, 1, tzinfo=UTC))
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))

        # WHY: mock at service level — the client delegates to the search service
        client.search.get_videos_for_person_and_date_range = AsyncMock(return_value=[a1, a2])

        result = await client.get_videos_for_all_persons(["person-1"], date_range)
        assert [a.id for a in result] == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_two_people_keep_only_their_shared_videos(self, _mock_config):
        from datetime import UTC, datetime

        from immich_memories.timeperiod import DateRange
        from tests.conftest import make_asset

        client = ImmichClient(_TEST_URL, _TEST_KEY)
        shared = make_asset("shared", file_created_at=datetime(2024, 2, 1, tzinfo=UTC))
        only_a = make_asset("only-a", file_created_at=datetime(2024, 1, 1, tzinfo=UTC))
        only_b = make_asset("only-b", file_created_at=datetime(2024, 3, 1, tzinfo=UTC))
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))

        async def mock_query(person_id, date_range, progress_callback=None):
            if person_id == "person-a":
                return [only_a, shared]
            return [shared, only_b]

        # WHY: mock at service level — the client delegates to the search service
        client.search.get_videos_for_person_and_date_range = AsyncMock(side_effect=mock_query)

        result = await client.get_videos_for_all_persons(["person-a", "person-b"], date_range)
        assert [a.id for a in result] == ["shared"]

    @pytest.mark.asyncio
    async def test_empty_person_list_returns_empty(self, _mock_config):
        from datetime import datetime

        from immich_memories.timeperiod import DateRange

        client = ImmichClient(_TEST_URL, _TEST_KEY)
        date_range = DateRange(start=datetime(2024, 1, 1), end=datetime(2024, 12, 31, 23, 59, 59))
        # WHY: mock at service level — the client delegates to the search service
        client.search.get_videos_for_person_and_date_range = AsyncMock()

        result = await client.get_videos_for_all_persons([], date_range)
        assert not result
        client.search.get_videos_for_person_and_date_range.assert_not_called()
