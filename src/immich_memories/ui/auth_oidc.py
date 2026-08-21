"""OIDC client wrapper using authlib with PKCE and singleton caching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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


def is_user_allowed(email: str, auth_config: AuthConfig) -> bool:
    """Whether an authenticated account may actually sign in.

    An empty allow-list admits anyone, which is what a single-user tenant wants
    and is the behaviour this had before the option existed. Once either list is
    set, an account with no email claim is refused rather than exempt -- an IdP
    that omits the claim must not become a way past the list.
    """
    emails = {e.strip().casefold() for e in auth_config.allowed_emails}
    domains = {d.strip().lstrip("@.").casefold() for d in auth_config.allowed_domains}
    if not emails and not domains:
        return True

    address = email.strip().casefold()
    if not address or "@" not in address:
        return False
    return address in emails or address.rpartition("@")[2] in domains


def get_end_session_url(auth_config: AuthConfig) -> str | None:
    """Return the IdP's end_session_endpoint, or None if unavailable."""
    if _oauth_instance is None:
        return None

    try:
        metadata = _oauth_instance.oidc.server_metadata
        return metadata.get("end_session_endpoint")
    except (AttributeError, TypeError):
        return None


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower() or None, parts.port or default_port


def validate_callback_origin(request: Request, public_url: str) -> bool:
    """Whether the callback arrived on the origin the app is published under.

    Only meaningful when `auth.public_url` is configured. Without it there is
    nothing trustworthy to compare against: Starlette builds `request.url` from
    the Host header, so checking one against the other is a tautology -- which
    is what this function used to do. Unconfigured, the control is the IdP,
    which redirects only to the redirect_uri registered with it.
    """
    if not public_url:
        return True
    return _origin(str(request.url)) == _origin(public_url)


def oidc_redirect_uri(derived_uri: str, public_url: str) -> str:
    """The redirect_uri to hand the IdP.

    Behind a reverse proxy the derived host is the internal one unless the
    forwarded headers are trusted, and the IdP rejects any redirect_uri that is
    not the registered one. A configured public URL pins it.
    """
    if not public_url:
        return derived_uri
    return public_url.rstrip("/") + urlsplit(derived_uri).path


def reset_oidc_client() -> None:
    """Reset the singleton — for use in tests only."""
    global _oauth_instance  # noqa: PLW0603
    _oauth_instance = None
