"""Tests for Immich API retry logic with exponential backoff."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from immich_memories.api.immich import (
    ImmichAPIError,
    ImmichAuthError,
    ImmichClient,
)

_TEST_URL = "https://immich.example.com"
_TEST_KEY = "test-api-key"


@pytest.fixture()
def _mock_config():
    """Fixture kept for test method signatures that reference it."""
    yield


def _make_client() -> ImmichClient:
    """Create an ImmichClient with a mocked httpx client."""
    client = ImmichClient(_TEST_URL, _TEST_KEY)
    client._client = AsyncMock()
    client._client.is_closed = False
    return client


class TestRetryOn503:
    """Retry on 503 Service Unavailable."""

    @pytest.mark.asyncio
    async def test_retries_on_503(self, _mock_config):
        """503 then 200 succeeds on second attempt."""
        client = _make_client()
        resp_503 = httpx.Response(503, request=httpx.Request("GET", "/test"))
        resp_200 = httpx.Response(
            200,
            request=httpx.Request("GET", "/test"),
            json={"ok": True},
            headers={"content-type": "application/json"},
        )
        # WHY: simulate transient 503 followed by success from Immich server
        client._client.request = AsyncMock(side_effect=[resp_503, resp_200])

        # WHY: avoid real sleep in tests — backoff would add seconds
        with patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test")

        assert result == {"ok": True}
        assert client._client.request.call_count == 2


class TestRetryOnTimeout:
    """Retry on network timeout."""

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self, _mock_config):
        """Timeout then 200 succeeds on second attempt."""
        client = _make_client()
        resp_200 = httpx.Response(
            200,
            request=httpx.Request("GET", "/test"),
            json={"data": 42},
            headers={"content-type": "application/json"},
        )
        # WHY: simulate network timeout followed by success from Immich server
        client._client.request = AsyncMock(
            side_effect=[httpx.TimeoutException("timed out"), resp_200]
        )

        # WHY: avoid real sleep in tests — backoff would add seconds
        with patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock):
            result = await client._request("GET", "/test")

        assert result == {"data": 42}
        assert client._client.request.call_count == 2


class TestGivesUpAfterMaxRetries:
    """Gives up after exhausting all retry attempts."""

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, _mock_config):
        """3x 503 raises ImmichAPIError after all retries exhausted."""
        client = _make_client()
        resp_503 = httpx.Response(503, request=httpx.Request("GET", "/test"))
        # WHY: simulate persistent server outage — all 3 attempts return 503
        client._client.request = AsyncMock(return_value=resp_503)

        # WHY: avoid real sleep in tests — backoff would add seconds
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ImmichAPIError, match="Server error: 503"),
        ):
            await client._request("GET", "/test")

        assert client._client.request.call_count == 3


class TestNoRetryOn401:
    """No retry on authentication errors."""

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self, _mock_config):
        """401 raises ImmichAuthError immediately without retry."""
        client = _make_client()
        resp_401 = httpx.Response(401, request=httpx.Request("GET", "/test"))
        # WHY: simulate invalid API key — should not retry auth failures
        client._client.request = AsyncMock(return_value=resp_401)

        with pytest.raises(ImmichAuthError, match="Invalid API key"):
            await client._request("GET", "/test")

        # Only one attempt — no retries for auth errors
        assert client._client.request.call_count == 1
