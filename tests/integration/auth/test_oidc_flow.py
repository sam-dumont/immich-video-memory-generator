"""OIDC integration tests — real oidc-provider-mock, real auth middleware."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from .conftest import make_test_app

pytestmark = pytest.mark.integration


class TestOIDCDiscovery:
    """Verify the mock OIDC server is reachable and well-configured."""

    def test_well_known_endpoint_reachable(self, oidc_server):
        url = f"http://localhost:{oidc_server.server_port}/.well-known/openid-configuration"
        resp = httpx.get(url)
        assert resp.status_code == 200

        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "userinfo_endpoint" in data
        assert "issuer" in data


class TestOIDCAuthorizeRedirect:
    """GET /auth/authorize should redirect to the IdP with correct params."""

    def test_authorize_redirects_to_idp(self, client, oidc_server, auth_config):
        resp = client.get("/auth/authorize")
        assert resp.status_code == 302

        location = resp.headers["location"]
        parsed = urlparse(location)

        # Should redirect to the mock IdP's authorize endpoint
        assert parsed.hostname == "localhost"
        assert parsed.port == oidc_server.server_port
        assert "/oauth2/authorize" in parsed.path

        params = parse_qs(parsed.query)
        assert params["client_id"] == [auth_config.client_id]
        assert "openid" in params["scope"][0]
        assert "state" in params
        # PKCE: code_challenge should be present
        assert "code_challenge" in params
        assert params["code_challenge_method"] == ["S256"]


class TestOIDCFullFlow:
    """Exercise the complete OIDC authorization code flow end-to-end."""

    def test_full_auth_code_flow(self, oidc_server, auth_config):
        server_url = f"http://localhost:{oidc_server.server_port}"

        # 1. Register a test user on the mock IdP
        user_resp = httpx.put(
            f"{server_url}/users/testuser",
            json={
                "preferred_username": "testuser",
                "email": "test@example.com",
                "name": "Test User",
            },
        )
        assert user_resp.status_code == 204

        # 2. Start the flow: GET /auth/authorize → IdP redirect
        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False, base_url="http://testserver")

        resp = client.get("/auth/authorize")
        assert resp.status_code == 302
        idp_authorize_url = resp.headers["location"]

        # 3. Follow redirect to the IdP's authorize page (GET)
        idp_resp = httpx.get(idp_authorize_url)
        assert idp_resp.status_code == 200

        # 4. POST to IdP authorize endpoint to grant consent
        #    The mock IdP expects `sub` in form data and the same query params
        parsed_auth_url = urlparse(idp_authorize_url)
        idp_authorize_base = (
            f"{parsed_auth_url.scheme}://{parsed_auth_url.netloc}{parsed_auth_url.path}"
        )

        consent_resp = httpx.post(
            f"{idp_authorize_base}?{parsed_auth_url.query}",
            data={"sub": "testuser"},
            follow_redirects=False,
        )
        assert consent_resp.status_code == 302

        # 5. The IdP redirects back to /auth/callback with code and state
        callback_url = consent_resp.headers["location"]
        assert "/auth/callback" in callback_url

        callback_parsed = urlparse(callback_url)
        callback_params = parse_qs(callback_parsed.query)
        assert "code" in callback_params
        assert "state" in callback_params

        # 6. Follow the callback to our app
        #    Need to pass the same session cookies from step 2
        callback_resp = client.get(callback_url)
        assert callback_resp.status_code == 307

        # Should redirect to /
        assert callback_resp.headers["location"] == "/"

        # 7. Now access the index with the session from callback
        index_resp = client.get("/")
        assert index_resp.status_code == 200

        body = index_resp.json()
        assert body["session"]["authenticated"] is True
        assert body["session"]["username"] == "testuser"
        assert body["session"]["email"] == "test@example.com"
        assert body["session"]["auth_provider"] == "oidc"


class TestOIDCCallbackErrors:
    """Error cases in the OIDC callback."""

    def test_callback_without_code(self, oidc_server, auth_config):
        """Missing auth code should fail with an error (not crash)."""
        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)

        # Hit callback without going through authorize (no state/code in session)
        resp = client.get("/auth/callback?state=bogus")
        # Should fail gracefully — either 400/500, not a crash
        assert resp.status_code >= 400

    def test_callback_with_invalid_state(self, oidc_server, auth_config):
        """Tampered state param should fail gracefully."""
        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)

        # Start a real authorize to get session state
        resp = client.get("/auth/authorize")
        assert resp.status_code == 302

        # Then hit callback with tampered state and no code
        resp = client.get("/auth/callback?code=fake&state=tampered")
        assert resp.status_code >= 400


class TestBasicAuthIntegration:
    """Basic auth end-to-end via the test app."""

    def test_basic_login_flow(self, basic_auth_config):
        app = make_test_app(basic_auth_config)
        client = TestClient(app, follow_redirects=False)

        # Login with correct credentials
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

        # Session should be set — access protected page
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session"]["authenticated"] is True
        assert body["session"]["username"] == "admin"

    def test_basic_wrong_credentials(self, basic_auth_config):
        app = make_test_app(basic_auth_config)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "invalid credentials"


class TestMiddlewareIntegration:
    """Auth middleware behavior with real routes."""

    def test_health_bypasses_auth(self, auth_config):
        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_protected_page_redirects(self, auth_config):
        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_authenticated_session_passes(self, oidc_server, auth_config):
        """After a successful OIDC flow, the session grants access."""
        server_url = f"http://localhost:{oidc_server.server_port}"

        # Register user
        httpx.put(
            f"{server_url}/users/sessionuser",
            json={"preferred_username": "sessionuser", "email": "s@test.com"},
        )

        app = make_test_app(auth_config)
        client = TestClient(app, follow_redirects=False, base_url="http://testserver")

        # Full OIDC flow
        resp = client.get("/auth/authorize")
        idp_url = resp.headers["location"]
        parsed = urlparse(idp_url)

        consent = httpx.post(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{parsed.query}",
            data={"sub": "sessionuser"},
            follow_redirects=False,
        )
        callback_url = consent.headers["location"]
        client.get(callback_url)

        # Now the session cookie is set — protected page should work
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["session"]["authenticated"] is True
