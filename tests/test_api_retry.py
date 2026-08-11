"""Tests for Immich API retry logic with exponential backoff."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
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


class TestTransportFailureRedaction:
    """Transport diagnostics never expose the configured Immich credential."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("make_error", "expected_attempts"),
        [
            pytest.param(
                lambda key: httpx.TimeoutException(f"timed out carrying {key}"),
                3,
                id="timeout-unlabelled",
            ),
            pytest.param(
                lambda key: httpx.TimeoutException(f"x-api-key={key} timed out"),
                3,
                id="timeout-labelled",
            ),
            pytest.param(
                lambda key: httpx.ConnectError(f"connection refused for {key}"),
                3,
                id="network-unlabelled",
            ),
            pytest.param(
                lambda key: httpx.ConnectError(f"api_key: {key} connection refused"),
                3,
                id="network-labelled",
            ),
            pytest.param(
                lambda key: httpx.RequestError(f"request rejected for {key}"),
                1,
                id="request-unlabelled",
            ),
            pytest.param(
                lambda key: httpx.RequestError(f"x-api-key: {key} request rejected"),
                1,
                id="request-labelled",
            ),
        ],
    )
    async def test_redacts_logs_exception_and_chain_without_changing_retries(
        self,
        _mock_config,
        caplog: pytest.LogCaptureFixture,
        make_error: Callable[[str], httpx.RequestError],
        expected_attempts: int,
    ) -> None:
        """Raw transport exceptions cannot escape through any diagnostic surface."""
        api_key = "configured-immich-secret-91de"
        client = ImmichClient(_TEST_URL, api_key)
        client._client = AsyncMock()
        client._client.is_closed = False
        client._client.request = AsyncMock(side_effect=make_error(api_key))

        # WHY: keep the real retry and logging path while removing only wall-clock delay.
        with (
            patch("immich_memories.api.immich.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.WARNING, logger="immich_memories.api.immich"),
            pytest.raises(ImmichAPIError) as raised,
        ):
            await client._request("GET", "/test")

        final_error = raised.value
        rendered_traceback = "".join(
            traceback.format_exception(raised.type, final_error, raised.tb)
        )
        diagnostics = "\n".join(
            [
                *(record.getMessage() for record in caplog.records),
                str(final_error),
                rendered_traceback,
                str(final_error.__cause__ or ""),
                str(final_error.__context__ or ""),
            ]
        )

        assert api_key not in diagnostics
        assert "***" in diagnostics
        assert final_error.__cause__ is None
        assert final_error.__context__ is None
        assert client._client.request.call_count == expected_attempts


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
