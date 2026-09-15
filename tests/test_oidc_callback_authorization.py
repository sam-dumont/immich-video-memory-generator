"""An email allow-list must not trust an unverified profile address."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from immich_memories.config_models_auth import AuthConfig


@pytest.mark.parametrize("verified", [False, None, "true", 1, True])
@pytest.mark.parametrize(
    "allow_list", [{"allowed_emails": ["owner@example.com"]}, {"allowed_domains": ["example.com"]}]
)
def test_callback_checks_email_ownership_before_creating_a_session(
    monkeypatch, verified, allow_list
):
    from immich_memories.ui import app as ui_app

    config = SimpleNamespace(
        auth=AuthConfig(
            enabled=True,
            provider="oidc",
            issuer_url="https://idp.example.com",
            client_id="memories",
            **allow_list,
        )
    )
    session = {}
    # WHY: the IdP exchange and browser session are external login boundaries.
    oauth = SimpleNamespace(
        oidc=SimpleNamespace(
            authorize_access_token=AsyncMock(
                return_value={
                    "userinfo": {
                        "sub": "different-account",
                        "email": "owner@example.com",
                        "email_verified": verified,
                    }
                }
            )
        )
    )
    monkeypatch.setattr(ui_app, "get_config", lambda: config)
    monkeypatch.setattr(ui_app, "app", SimpleNamespace(storage=SimpleNamespace(user=session)))
    monkeypatch.setattr("immich_memories.ui.auth_oidc.create_oidc_client", lambda _config: oauth)
    server = FastAPI()
    server.add_api_route("/auth/callback", ui_app._oidc_callback, methods=["GET"])

    with TestClient(server) as client:
        response = client.get("/auth/callback", follow_redirects=False)

    assert response.status_code == (307 if verified is True else 403)
    assert bool(session.get("authenticated")) is (verified is True)
