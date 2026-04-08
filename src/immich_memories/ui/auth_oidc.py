"""OIDC client wrapper using authlib with PKCE and singleton caching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]
    from starlette.requests import Request

    from immich_memories.config_models_auth import AuthConfig

logger = logging.getLogger(__name__)

_oauth_instance: OAuth | None = None


def _import_authlib() -> type[OAuth]:
    """Lazy-import authlib with a helpful error if missing."""
    try:
        from authlib.integrations.starlette_client import OAuth
    except ImportError:
        raise ImportError(
            "authlib is required for OIDC authentication. "
            "Install it with: pip install 'immich-memories[auth]'"
        ) from None
    return OAuth


def create_oidc_client(auth_config: AuthConfig) -> OAuth:
    """Create or return the cached authlib OAuth singleton.

    authlib stores PKCE code_verifier and CSRF state in the client instance
    between authorize_redirect and authorize_access_token calls. Using
    different instances would cause state lookup failures.
    """
    global _oauth_instance  # noqa: PLW0603

    if _oauth_instance is not None:
        return _oauth_instance

    oauth_cls = _import_authlib()
    oauth = oauth_cls()

    server_metadata_url = f"{auth_config.issuer_url.rstrip('/')}/.well-known/openid-configuration"

    oauth.register(
        name="oidc",
        client_id=auth_config.client_id,
        client_secret=auth_config.client_secret or None,
        server_metadata_url=server_metadata_url,
        code_challenge_method="S256",
        client_kwargs={"scope": auth_config.scope},
    )

    _oauth_instance = oauth
    return oauth


def extract_user_from_token(token: dict[str, Any]) -> tuple[str, str]:
    """Extract (username, email) from an OIDC token response.

    Username priority: preferred_username > name > sub > "unknown".
    """
    userinfo = token.get("userinfo", {})
    if not userinfo:
        return ("unknown", "")

    username = (
        userinfo.get("preferred_username")
        or userinfo.get("name")
        or userinfo.get("sub")
        or "unknown"
    )
    email = userinfo.get("email", "")
    return (username, email)


def get_end_session_url(auth_config: AuthConfig) -> str | None:
    """Return the IdP's end_session_endpoint, or None if unavailable."""
    if _oauth_instance is None:
        return None

    try:
        metadata = _oauth_instance.oidc.server_metadata
        return metadata.get("end_session_endpoint")  # type: ignore[no-any-return]
    except (AttributeError, TypeError):
        return None


def validate_callback_origin(request: Request) -> bool:
    """Ensure the OIDC callback URL belongs to the same origin as the app.

    Rejects callbacks where the Host header doesn't match the request URL,
    which would indicate an open-redirect attack via a crafted redirect_uri.
    """
    callback_url = str(request.url)
    parsed = urlparse(callback_url)

    expected_host = request.headers.get("host", "")
    # Strip port from parsed netloc for comparison
    callback_host = parsed.netloc

    if not expected_host or not callback_host:
        return False

    # WHY: compare full netloc (host:port) to handle non-standard ports
    return callback_host == expected_host


def reset_oidc_client() -> None:
    """Reset the singleton — for use in tests only."""
    global _oauth_instance  # noqa: PLW0603
    _oauth_instance = None
