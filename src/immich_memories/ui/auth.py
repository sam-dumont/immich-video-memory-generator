"""Provider-agnostic authentication helpers.

The actual middleware is registered via @app.middleware('http') in app.py,
NOT via BaseHTTPMiddleware (which breaks NiceGUI websockets).
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
from collections.abc import MutableMapping
from datetime import UTC, datetime

from immich_memories.config_models_auth import AuthConfig

logger = logging.getLogger(__name__)

_BYPASS_PREFIXES = ("/_nicegui/",)
_BYPASS_EXACT = frozenset({"/health", "/login", "/logout", "/auth/callback", "/auth/authorize"})


def is_bypass_path(path: str) -> bool:
    """Check if a path should bypass authentication."""
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _BYPASS_PREFIXES)


def verify_credentials(username: str, password: str, auth_config: AuthConfig) -> bool:
    """Check username/password against config using constant-time comparison.

    Both username and password are compared with secrets.compare_digest
    to prevent timing-based side-channel attacks.
    """
    username_ok = secrets.compare_digest(username, auth_config.username)
    password_ok = secrets.compare_digest(password, auth_config.password)
    return username_ok and password_ok


def _parse_proxy_networks(
    trusted_proxies: list[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse proxy strings into network objects, skipping invalid entries."""
    networks = []
    for proxy in trusted_proxies:
        try:
            # ip_network handles both CIDR ("10.0.0.0/24") and single IPs ("10.0.0.1" -> /32)
            networks.append(ipaddress.ip_network(proxy, strict=False))
        except ValueError:
            continue
    return networks


def is_trusted_proxy(client_ip: str, trusted_proxies: list[str]) -> bool:
    """Check if client_ip matches any entry in trusted_proxies.

    Supports exact IP addresses and CIDR notation. Invalid entries are
    silently skipped. Returns False for invalid client IPs or empty lists.
    """
    if not trusted_proxies:
        return False

    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    return any(addr in network for network in _parse_proxy_networks(trusted_proxies))


def set_session(
    session: MutableMapping[str, object],
    *,
    username: str,
    provider: str,
    email: str = "",
) -> None:
    """Populate session with authentication state."""
    session["authenticated"] = True
    session["username"] = username
    session["auth_provider"] = provider
    session["email"] = email
    session["authenticated_at"] = datetime.now(UTC).isoformat()


_SESSION_KEYS = ("authenticated", "username", "email", "auth_provider", "authenticated_at")


def clear_session(session: MutableMapping[str, object]) -> None:
    """Remove all authentication-related keys from the session."""
    for key in _SESSION_KEYS:
        session.pop(key, None)  # type: ignore[arg-type]


def is_auth_enabled(auth_config: AuthConfig) -> bool:
    """Check whether authentication is enabled in config."""
    return auth_config.enabled
