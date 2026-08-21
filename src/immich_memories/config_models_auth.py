"""Authentication configuration model for Immich Memories."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from immich_memories.config_models import expand_env_vars


def _normalised_public_url(value: str) -> str:
    """Reject anything that is not an absolute http(s) URL, and drop the trailing slash."""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("public_url must be an absolute http(s) URL, e.g. https://host")
    return value.rstrip("/")


class AuthConfig(BaseModel):
    """Authentication settings for the web UI."""

    enabled: bool = False
    provider: Literal["basic", "oidc", "header"] = "basic"
    session_ttl_hours: int = Field(default=24, ge=1, le=720)

    # Basic auth
    username: str = ""
    password: str = ""

    # The externally reachable base URL of this app, e.g.
    # "https://memories.example.com". Pins the OIDC redirect_uri behind a
    # reverse proxy and makes callback-origin validation possible; without it
    # there is nothing trustworthy to compare an incoming Host header against.
    public_url: str = ""

    # OIDC
    issuer_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scope: str = "openid email profile"
    auto_launch: bool = False
    button_text: str = "Sign in with SSO"

    # Trusted header SSO
    user_header: str = "Remote-User"
    email_header: str = "Remote-Email"
    trusted_proxies: list[str] = Field(default_factory=list)

    @field_validator("password", "client_secret", "issuer_url", "client_id", mode="before")
    @classmethod
    def expand_env(cls, v: str) -> str:
        if isinstance(v, str):
            return expand_env_vars(v)
        return v

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> AuthConfig:
        """Check public_url, then the fields the active provider requires."""
        if self.public_url:
            self.public_url = _normalised_public_url(self.public_url)

        if not self.enabled:
            return self

        if self.provider == "basic":
            if not self.username:
                raise ValueError("username is required when provider is 'basic'")
            if not self.password:
                raise ValueError("password is required when provider is 'basic'")

        elif self.provider == "oidc":
            if not self.issuer_url:
                raise ValueError("issuer_url is required when provider is 'oidc'")
            if not self.client_id:
                raise ValueError("client_id is required when provider is 'oidc'")

        elif self.provider == "header":
            if not self.trusted_proxies:
                raise ValueError("trusted_proxies must be non-empty when provider is 'header'")

        return self
