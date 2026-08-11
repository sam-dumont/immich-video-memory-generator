"""Immich API-version compatibility policy and resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import assert_never


class ApiVersionPolicy(StrEnum):
    """Configured strategy for selecting the Immich API version."""

    AUTO = "auto"
    V2 = "v2"
    V3 = "v3"


class ResolvedApiVersion(StrEnum):
    """Immich API version selected for requests."""

    V2 = "v2"
    V3 = "v3"


class UnsupportedImmichVersion(RuntimeError):
    """Raised when automatic detection finds an unsupported server version."""


def resolve_api_version(policy: ApiVersionPolicy, server_major: int | None) -> ResolvedApiVersion:
    """Resolve a configured policy against an optional detected server major."""
    if policy is ApiVersionPolicy.V2:
        return ResolvedApiVersion.V2
    if policy is ApiVersionPolicy.V3:
        return ResolvedApiVersion.V3
    if policy is ApiVersionPolicy.AUTO:
        if server_major == 2:
            return ResolvedApiVersion.V2
        if server_major == 3:
            return ResolvedApiVersion.V3
        raise UnsupportedImmichVersion(
            f"Unsupported Immich major version {server_major}. "
            "Supported major versions are 2 and 3. "
            "Set immich.api_version to 'v2' or 'v3' to use an explicit override."
        )
    assert_never(policy)
