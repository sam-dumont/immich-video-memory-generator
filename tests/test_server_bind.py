"""Secure-by-default binding (#476): auth off means localhost unless the
operator says otherwise — in config or on the command line."""

from __future__ import annotations

from immich_memories.config_loader import Config


def _config(**overrides) -> Config:
    return Config(**overrides)


class TestEffectiveHost:
    def test_auth_off_and_nothing_set_binds_localhost(self):
        config = _config()

        host = config.server.effective_host(auth_enabled=config.auth.enabled)

        assert host == "127.0.0.1"

    def test_auth_on_keeps_the_configured_default(self):
        config = _config()

        # auth validity is AuthConfig's concern; the resolver only needs the flag
        assert config.server.effective_host(auth_enabled=True) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind

    def test_an_explicit_host_is_the_operators_decision(self):
        config = _config(server={"host": "0.0.0.0"})  # noqa: S104 — explicit operator choice under test

        assert config.server.effective_host(auth_enabled=False) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind

    def test_the_named_escape_hatch_works(self):
        config = _config(server={"allow_unauthenticated_lan": True})

        assert config.server.effective_host(auth_enabled=False) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind
