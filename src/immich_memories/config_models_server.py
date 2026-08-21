"""UI server settings — bind address, port, and the secure-by-default rule."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """UI server settings (host, port)."""

    host: str = Field(default="0.0.0.0", description="Listen address (IPv4, IPv6, or hostname)")  # noqa: S104
    port: int = Field(default=8080, ge=1, le=65535, description="Listen port")
    enable_demo_mode: bool = Field(
        default=False, description="Show demo/privacy toggle in sidebar (for screenshots/E2E)"
    )
    secure_cookies: bool = Field(
        default=False,
        description="Mark the session cookie Secure (only when every visitor arrives over HTTPS)",
    )
    allow_unauthenticated_lan: bool = Field(
        default=False,
        description=(
            "Bind beyond localhost even with authentication disabled. The name "
            "spells out the risk: anyone who can reach the port can use the UI "
            "and, through it, the Immich library."
        ),
    )

    def effective_host(self, *, auth_enabled: bool) -> str:
        """The address to bind, secure by default (#476).

        With authentication disabled and no explicit decision — no
        ``server.host`` in the config, no escape hatch — the UI binds
        localhost. An explicitly configured host (or a --host flag upstream,
        like the Docker image's CMD) is the operator's decision and wins.
        """
        if auth_enabled or self.allow_unauthenticated_lan or "host" in self.model_fields_set:
            return self.host
        return "127.0.0.1"
