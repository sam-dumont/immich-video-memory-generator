"""Provider-agnostic authentication helpers.

The actual middleware is registered via @app.middleware('http') in app.py,
NOT via BaseHTTPMiddleware (which breaks NiceGUI websockets).
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
from collections.abc import MutableMapping
from datetime import UTC, datetime

from immich_memories.config_models_auth import AuthConfig

logger = logging.getLogger(__name__)

# ---------- brute-force rate limiter ----------

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 600  # 10 minutes

# {ip: list[datetime]} — guarded by _rate_lock
_failed_attempts: dict[str, list[datetime]] = {}
_rate_lock = threading.Lock()


def _cleanup_stale_entries(now: datetime) -> None:
    """Remove entries whose timestamps are all outside the window."""
    cutoff = now.timestamp() - _WINDOW_SECONDS
    stale_keys = [
        ip
        for ip, timestamps in _failed_attempts.items()
        if all(ts.timestamp() < cutoff for ts in timestamps)
    ]
    for key in stale_keys:
        del _failed_attempts[key]


def record_failed_login(ip: str) -> None:
    """Record a failed login attempt from *ip*."""
    now = datetime.now(UTC)
    with _rate_lock:
        _cleanup_stale_entries(now)
        _failed_attempts.setdefault(ip, []).append(now)


def is_rate_limited(ip: str) -> bool:
    """Return True if *ip* has exceeded the failure threshold."""
    now = datetime.now(UTC)
    cutoff = now.timestamp() - _WINDOW_SECONDS
    with _rate_lock:
        attempts = _failed_attempts.get(ip, [])
        recent = [ts for ts in attempts if ts.timestamp() >= cutoff]
        _failed_attempts[ip] = recent
        return len(recent) >= _MAX_ATTEMPTS


def reset_rate_limiter() -> None:
    """Clear all rate-limit state -- for tests only."""
    with _rate_lock:
        _failed_attempts.clear()


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
