"""Provider-agnostic authentication helpers.

The actual middleware is registered via @app.middleware('http') in app.py,
NOT via BaseHTTPMiddleware (which breaks NiceGUI websockets).
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime

import nicegui

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


# WHY: only NiceGUI's versioned framework assets (JS/CSS the login page needs) and
# the app's own /static (fonts) are public. "/_nicegui/auto/{media,static}/..." serves
# every locally previewed video/audio file and must stay behind the login.
_BYPASS_PREFIXES = (f"/_nicegui/{nicegui.__version__}/", "/static/")
_HEALTH_BYPASS_EXACT = frozenset({"/health", "/health/live", "/health/ready"})
_BYPASS_EXACT = (
    frozenset(
        {
            "/login",
            "/logout",
            "/auth/callback",
            "/auth/authorize",
        }
    )
    | _HEALTH_BYPASS_EXACT
)


def is_health_probe_path(path: str) -> bool:
    """Return whether path is an exact operational health endpoint."""
    return path in _HEALTH_BYPASS_EXACT


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


# The HTTP trigger API. Not a bypass path: a caller without a valid token still
# has to be a logged-in session, which is what the auth middleware decides.
_TRIGGER_PREFIX = "/api/trigger"


def is_trigger_path(path: str) -> bool:
    """Whether a path belongs to the HTTP trigger API."""
    return path == _TRIGGER_PREFIX or path.startswith(f"{_TRIGGER_PREFIX}/")


def presented_trigger_token(headers: Mapping[str, str]) -> str:
    """The trigger token a caller offered, from either header the API accepts.

    `x-api-key` is what Immich's own API takes, so a workflow calling us looks
    like a workflow calling Immich; `Authorization: Bearer` is what everything
    else reaches for. Anything else counts as no offer at all.
    """
    if api_key := headers.get("x-api-key", ""):
        return api_key
    scheme, _, value = headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def trigger_token_matches(presented: str, configured: str) -> bool:
    """Constant-time comparison that refuses an unset token.

    Without the emptiness guard an operator who never set `server.trigger_token`
    would be authorizing every caller who also sends nothing.
    """
    if not configured or not presented:
        return False
    return secrets.compare_digest(presented, configured)


def trigger_token_authorizes(path: str, headers: Mapping[str, str], configured_token: str) -> bool:
    """Whether a trigger token lets this request skip the session check.

    The middleware asks this before anything reads `app.storage.user`: a headless
    caller sends no session cookie, so touching NiceGUI's user store for one would
    both fail and mint a session file per request.
    """
    if not is_trigger_path(path):
        return False
    return trigger_token_matches(presented_trigger_token(headers), configured_token)


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
        session.pop(key, None)


def is_auth_enabled(auth_config: AuthConfig) -> bool:
    """Check whether authentication is enabled in config."""
    return auth_config.enabled


def client_ip_for_rate_limit(
    *,
    peer_ip: str,
    forwarded_for: str | None,
    auth_config: AuthConfig,
) -> str:
    """The IP the login limiter should bucket on (S3).

    Behind Traefik/nginx every request carries the proxy's address, so one
    bad actor locks out everyone (5 failures = a 10-minute global lockout).
    X-Forwarded-For fixes that, but only from a peer we trust: an untrusted
    caller reaching the port directly could otherwise pick its own bucket —
    or evade the limiter entirely — by setting the header itself.
    """
    if not forwarded_for or not is_trusted_proxy(peer_ip, auth_config.trusted_proxies):
        return peer_ip
    # Leftmost is the originating client as recorded by the first proxy; the
    # chain is only as trustworthy as the peer that handed it to us.
    candidate = forwarded_for.split(",")[0].strip()
    return candidate or peer_ip
