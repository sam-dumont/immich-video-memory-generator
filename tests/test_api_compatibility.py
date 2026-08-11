"""Tests for Immich API-version compatibility policy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from immich_memories.api.compatibility import (
    ApiVersionPolicy,
    ResolvedApiVersion,
    UnsupportedImmichVersion,
    resolve_api_version,
)
from immich_memories.api.immich import ImmichAPIError, ImmichClient
from immich_memories.config_loader import Config
from immich_memories.preflight import CheckStatus, check_immich

_TEST_URL = "https://immich.example.com"
_TEST_KEY = "test-api-key"


def test_preflight_passes_configured_api_version_to_client() -> None:
    config = Config(immich={"url": _TEST_URL, "api_key": _TEST_KEY, "api_version": "v2"})
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_api_version.return_value = ResolvedApiVersion.V2
    client.get_current_user.return_value = SimpleNamespace(name="Sam", email="sam@example.com")

    with patch(
        "immich_memories.api.immich.SyncImmichClient", return_value=client
    ) as client_factory:
        result = check_immich(config)

    assert result.status is CheckStatus.OK
    client_factory.assert_called_once_with(
        base_url=_TEST_URL,
        api_key=_TEST_KEY,
        api_version=ApiVersionPolicy.V2,
    )
    client.get_api_version.assert_called_once_with()
    assert result.details == f"Server: {_TEST_URL}; API: v2"


def test_auto_preflight_reports_detected_server_major() -> None:
    config = Config(immich={"url": _TEST_URL, "api_key": _TEST_KEY})
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_api_version.return_value = ResolvedApiVersion.V3
    client.get_current_user.return_value = SimpleNamespace(name="Sam", email="sam@example.com")

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result.status is CheckStatus.OK
    assert result.details == f"Server: {_TEST_URL}; API: v3"


def test_auto_preflight_rejects_unsupported_server_major() -> None:
    config = Config(immich={"url": _TEST_URL, "api_key": _TEST_KEY})
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_api_version.side_effect = UnsupportedImmichVersion(
        "Unsupported Immich major version 4"
    )

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result.status is CheckStatus.ERROR
    assert result.message == "Unsupported Immich version"
    assert result.details == "Unsupported Immich major version 4"
    client.get_current_user.assert_not_called()


def _client_with_version_response(
    payload: object,
) -> tuple[ImmichClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = ImmichClient(_TEST_URL, _TEST_KEY)
    client._client = httpx.AsyncClient(
        base_url=_TEST_URL,
        transport=httpx.MockTransport(handler),
    )
    return client, requests


@pytest.fixture(
    params=[
        pytest.param(
            ({"major": 2, "minor": 9, "patch": 1}, ResolvedApiVersion.V2),
            id="v2",
        ),
        pytest.param(
            ({"major": 3, "minor": 1, "patch": 0}, ResolvedApiVersion.V3),
            id="v3",
        ),
    ]
)
async def version_client(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[ImmichClient, list[httpx.Request], ResolvedApiVersion]]:
    payload, expected = request.param
    client, requests = _client_with_version_response(payload)
    yield client, requests, expected
    await client.close()


@pytest.mark.asyncio
async def test_auto_detects_supported_server_version(
    version_client: tuple[ImmichClient, list[httpx.Request], ResolvedApiVersion],
) -> None:
    client, requests, expected = version_client

    result = await client.get_api_version()

    assert result is expected
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/server/version")
    ]


@pytest.mark.asyncio
async def test_auto_version_cache_survives_close(
    version_client: tuple[ImmichClient, list[httpx.Request], ResolvedApiVersion],
) -> None:
    client, requests, expected = version_client
    assert await client.get_api_version() is expected
    assert await client.get_api_version() is expected

    await client.close()

    with patch(
        "immich_memories.api.immich.httpx.AsyncClient",
        side_effect=AssertionError("cached version must not recreate an HTTP client"),
    ):
        assert await client.get_api_version() is expected
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        pytest.param("v2", ResolvedApiVersion.V2, id="v2"),
        pytest.param("v3", ResolvedApiVersion.V3, id="v3"),
    ],
)
async def test_explicit_version_resolves_without_http(
    policy: str, expected: ResolvedApiVersion
) -> None:
    client = ImmichClient(_TEST_URL, _TEST_KEY, api_version=policy)

    # WHY: an explicit policy must resolve before the external HTTP boundary is created.
    with patch(
        "immich_memories.api.immich.httpx.AsyncClient",
        side_effect=AssertionError("explicit version must not create an HTTP client"),
    ):
        assert await client.get_api_version() is expected
    assert client._client is None


def test_client_rejects_unknown_api_version_policy() -> None:
    with pytest.raises(
        ValueError,
        match="api_version must be one of 'auto', 'v2', or 'v3'; got 'v4'",
    ):
        ImmichClient(_TEST_URL, _TEST_KEY, api_version="v4")


@pytest.mark.asyncio
async def test_concurrent_auto_version_calls_probe_once(
    version_client: tuple[ImmichClient, list[httpx.Request], ResolvedApiVersion],
) -> None:
    client, requests, expected = version_client

    results = await asyncio.gather(*(client.get_api_version() for _ in range(10)))

    assert results == [expected] * 10
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_auto_version_resolution_logs_server_policy_and_result(
    version_client: tuple[ImmichClient, list[httpx.Request], ResolvedApiVersion],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _requests, expected = version_client

    with caplog.at_level(logging.INFO, logger="immich_memories.api.immich"):
        await client.get_api_version()

    expected_server = "2.9.1" if expected is ResolvedApiVersion.V2 else "3.1.0"
    assert caplog.messages == [
        f"Immich API compatibility: server={expected_server} mode=auto resolved={expected.value}"
    ]


@pytest.mark.asyncio
async def test_auto_version_rejects_unsupported_server_major() -> None:
    client, requests = _client_with_version_response({"major": 4, "minor": 0, "patch": 0})
    try:
        with pytest.raises(UnsupportedImmichVersion) as raised:
            await client.get_api_version()
    finally:
        await client.close()

    message = str(raised.value)
    assert "major version 4" in message
    assert "Supported major versions are 2 and 3" in message
    assert "immich.api_version" in message
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"major": "not-a-number", "minor": 1, "patch": 0},
            id="non-numeric-major",
        ),
        pytest.param([], id="not-an-object"),
    ],
)
async def test_auto_version_rejects_malformed_server_version_response(payload: object) -> None:
    client, requests = _client_with_version_response(payload)
    try:
        with pytest.raises(ImmichAPIError) as raised:
            await client.get_api_version()
    finally:
        await client.close()

    message = str(raised.value)
    assert "Malformed Immich server version response" in message
    assert "/api/server/version" in message
    assert "numeric major, minor, and patch" in message
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("server_major", "expected"),
    [
        pytest.param(2, ResolvedApiVersion.V2, id="v2"),
        pytest.param(3, ResolvedApiVersion.V3, id="v3"),
    ],
)
def test_auto_resolves_supported_server_majors(
    server_major: int, expected: ResolvedApiVersion
) -> None:
    assert resolve_api_version(ApiVersionPolicy.AUTO, server_major) is expected


@pytest.mark.parametrize("server_major", [pytest.param(4, id="unknown"), pytest.param(None)])
def test_auto_rejects_unsupported_server_majors(server_major: int | None) -> None:
    with pytest.raises(UnsupportedImmichVersion) as raised:
        resolve_api_version(ApiVersionPolicy.AUTO, server_major)

    message = str(raised.value)
    assert f"major version {server_major}" in message
    assert "Supported major versions are 2 and 3" in message
    assert "immich.api_version" in message
    assert "'v2' or 'v3'" in message


@pytest.mark.parametrize(
    ("policy", "server_major", "expected"),
    [
        pytest.param(ApiVersionPolicy.V2, 3, ResolvedApiVersion.V2, id="v2-conflict"),
        pytest.param(ApiVersionPolicy.V3, 2, ResolvedApiVersion.V3, id="v3-conflict"),
        pytest.param(ApiVersionPolicy.V2, None, ResolvedApiVersion.V2, id="v2-missing"),
        pytest.param(ApiVersionPolicy.V3, None, ResolvedApiVersion.V3, id="v3-missing"),
    ],
)
def test_explicit_policy_ignores_detected_server_major(
    policy: ApiVersionPolicy,
    server_major: int | None,
    expected: ResolvedApiVersion,
) -> None:
    assert resolve_api_version(policy, server_major) is expected
