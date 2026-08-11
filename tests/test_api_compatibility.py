"""Tests for Immich API-version compatibility policy."""

from __future__ import annotations

import pytest

from immich_memories.api.compatibility import (
    ApiVersionPolicy,
    ResolvedApiVersion,
    UnsupportedImmichVersion,
    resolve_api_version,
)


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
