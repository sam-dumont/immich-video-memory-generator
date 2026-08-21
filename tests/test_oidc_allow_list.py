"""Authenticating with the IdP is not the same as being allowed in.

`extract_user_from_token` accepts whoever the IdP returns and `_oidc_callback`
creates a session from it, so on a tenant with social sign-ups any Google
account becomes an admin of this app, and on a shared Keycloak/Authentik realm
every account in the realm does. Immich defaults the same way but makes "auto
register" an explicit switch; there was no equivalent here.
"""

from __future__ import annotations

import pytest

from immich_memories.config_models_auth import AuthConfig
from immich_memories.ui.auth_oidc import is_user_allowed

_BASE = {"enabled": True, "provider": "oidc", "issuer_url": "https://idp", "client_id": "app"}


def _config(**kwargs) -> AuthConfig:
    return AuthConfig(**_BASE, **kwargs)


class TestUnconfigured:
    def test_an_empty_allow_list_admits_anyone(self):
        """Today's behaviour, preserved: a single-user tenant needs no list."""
        assert is_user_allowed("someone@anywhere.example", _config())


class TestAllowedEmails:
    def test_a_listed_address_is_admitted(self):
        config = _config(allowed_emails=["me@example.com", "you@example.com"])

        assert is_user_allowed("you@example.com", config)

    def test_an_unlisted_address_is_refused(self):
        config = _config(allowed_emails=["me@example.com"])

        assert not is_user_allowed("stranger@example.com", config)

    def test_the_comparison_ignores_case(self):
        """IdPs are inconsistent about case in the email claim."""
        config = _config(allowed_emails=["Me@Example.COM"])

        assert is_user_allowed("me@example.com", config)


class TestAllowedDomains:
    def test_an_address_in_a_listed_domain_is_admitted(self):
        config = _config(allowed_domains=["example.com"])

        assert is_user_allowed("anyone@example.com", config)

    def test_another_domain_is_refused(self):
        config = _config(allowed_domains=["example.com"])

        assert not is_user_allowed("anyone@evil.example", config)

    def test_a_lookalike_suffix_is_refused(self):
        """`endswith("example.com")` would admit this; it must not."""
        config = _config(allowed_domains=["example.com"])

        assert not is_user_allowed("anyone@notexample.com", config)

    def test_a_subdomain_is_refused_unless_listed(self):
        config = _config(allowed_domains=["example.com"])

        assert not is_user_allowed("anyone@sub.example.com", config)

    def test_a_leading_at_or_dot_in_the_config_is_tolerated(self):
        """People write it all three ways; none of them should silently fail."""
        for written in ("example.com", "@example.com", ".example.com"):
            assert is_user_allowed("a@example.com", _config(allowed_domains=[written]))


class TestEitherListAdmits:
    def test_a_listed_address_outside_the_listed_domain_is_admitted(self):
        config = _config(
            allowed_emails=["contractor@other.example"], allowed_domains=["example.com"]
        )

        assert is_user_allowed("contractor@other.example", config)
        assert is_user_allowed("staff@example.com", config)


class TestNoEmailClaim:
    def test_a_token_without_an_email_is_refused_when_a_list_exists(self):
        """An IdP that omits the claim must not become a way past the list."""
        config = _config(allowed_domains=["example.com"])

        assert not is_user_allowed("", config)

    def test_it_is_still_admitted_when_no_list_is_configured(self):
        assert is_user_allowed("", _config())


class TestConfigValidation:
    def test_an_entry_without_an_at_sign_is_refused_as_an_email(self):
        with pytest.raises(ValueError, match="allowed_emails"):
            _config(allowed_emails=["not-an-address"])
