"""Settings the UI server needs when it sits behind a TLS-terminating reverse proxy.

Turns config into the ``ui.run(**kwargs)`` entries NiceGUI forwards to Starlette's
SessionMiddleware (``session_middleware_kwargs``) and to uvicorn (``forwarded_allow_ips``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from immich_memories.config_loader import Config


def reverse_proxy_run_kwargs(config: Config, environ: Mapping[str, str]) -> dict[str, Any]:
    """Return the ``ui.run`` kwargs derived from ``server`` and ``auth`` config.

    ``auth.trusted_proxies`` becomes uvicorn's ``forwarded_allow_ips`` so ``X-Forwarded-For``
    and ``X-Forwarded-Proto`` from those proxies rewrite ``request.client`` (login rate
    limiter) and ``request.url`` (OIDC ``redirect_uri``). An explicit ``FORWARDED_ALLOW_IPS``
    env var is left for uvicorn to read, so it keeps winning over config.
    """
    kwargs: dict[str, Any] = {}
    if config.server.secure_cookies:
        kwargs["session_middleware_kwargs"] = {"https_only": True}
    if "FORWARDED_ALLOW_IPS" not in environ:
        if config.auth.provider == "header":
            # WHY: header auth trusts the proxy by the address it connects from. Once uvicorn
            # rewrites request.client to X-Forwarded-For, that check would see the visitor.
            kwargs["forwarded_allow_ips"] = []
        elif config.auth.trusted_proxies:
            kwargs["forwarded_allow_ips"] = config.auth.trusted_proxies.copy()
    return kwargs
